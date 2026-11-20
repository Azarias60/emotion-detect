"""Reporting 用例：报告建立、增量记录、账号范围观察与删除。

报告是同一轮检测业务数据的唯一事实来源：从识别启动时建立、归发起账号，
各过程按报告标识把所属进度与结果提供给同一份报告。观察是报告能力的
对外契约——观察开始取得当前账号范围的事实，随后随创建/修改/删除收到
更新；停止观察只结束订阅，不改变报告存续。标题与摘要由显示端从事实
派生，不是报告业务字段。
"""
import json
import threading
from datetime import date, datetime, time, timezone

from server import settings
from server.support.reporting.types.errors import ReportNotFoundError
from server.support.reporting.types.ports import ReportRepository

REPORT_TYPES = ("health", "fatigue", "emotion")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def report_day_bounds(start, end):
    """把用户选择的本地日期边界换算为 UTC 时间戳，供 created_at 比较。

    报告时间戳以 UTC 存储；直接拿本地日期字符串比较会把本地次日零点后的
    记录算进前一天。start 取当日零点、end 取当日 23:59:59，均按 REPORT_TZ
    解释后转为 UTC。空值返回 None；无法解析的非空日期抛 ValueError。
    """
    def _bound(value, end_of_day):
        if value in (None, ""):
            return None
        day = value if isinstance(value, date) else date.fromisoformat(str(value).strip())
        moment = time(23, 59, 59) if end_of_day else time.min
        return datetime.combine(day, moment, tzinfo=settings.REPORT_TZ).astimezone(timezone.utc).isoformat(timespec="seconds")

    return _bound(start, False), _bound(end, True)


def serialize_report(row):
    """报告对外只暴露业务事实：标识、类型、归属、创建时间与结果。"""
    return {
        "id": row["id"], "type": row["report_type"],
        "createUserId": row["create_user_id"], "createTime": row["created_at"],
        "result": json.loads(row["result_json"]),
    }


class ReportingService:
    def __init__(self, reports: ReportRepository):
        self.reports = reports
        self._observers: dict[int, set] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 账号侧入口：归属由调用方账号决定
    # ------------------------------------------------------------------
    def list_reports(self, owner_id):
        """账号全量报告集合；匹配筛选由消费端从集合派生。"""
        return {"items": [serialize_report(row) for row in self.reports.list_reports(owner_id)]}

    def report_detail(self, owner_id, report_id):
        row = self.reports.get_report(owner_id, report_id)
        if row is None:
            raise ReportNotFoundError("报告不存在")
        return serialize_report(row)

    def delete_report(self, owner_id, report_id):
        if not self.reports.delete_report(owner_id, report_id):
            raise ReportNotFoundError("报告不存在")
        self._publish(owner_id)

    def create_report(self, owner_id, kind):
        """识别启动时建立所属报告；内容随后由各过程增量提供。"""
        created = self.reports.insert_report(owner_id, kind, "{}", utc_now())
        self._publish(owner_id)
        return created

    def update_report(self, owner_id, report_id, facts):
        """账号侧对所属报告的增量修改；不存在的报告不可修改或复活。"""
        if self.reports.update_facts(report_id, facts, utc_now(), owner_id=owner_id) is None:
            raise ReportNotFoundError("报告不存在")
        self._publish(owner_id)

    def observe(self, owner_id, listener):
        """观察账号范围内的报告事实：立即取得当前集合，随后随变化更新。"""
        with self._lock:
            self._observers.setdefault(owner_id, set()).add(listener)
        listener(self._collection(owner_id))
        return self._release(owner_id, listener)

    # ------------------------------------------------------------------
    # 后端所属过程入口：一次执行开始时已绑定报告目标，按标识读写
    # ------------------------------------------------------------------
    def report_facts(self, report_id):
        row = self.reports.get_report_by_id(report_id)
        if row is None:
            raise ReportNotFoundError("报告不存在")
        return serialize_report(row)

    def record_facts(self, report_id, facts):
        """所属过程向同一份报告增量提供事实；报告已删除时不得复活。"""
        owner = self.reports.update_facts(report_id, facts, utc_now())
        if owner is None:
            raise ReportNotFoundError("报告不存在")
        self._publish(owner)

    # ------------------------------------------------------------------
    # 内部：观察登记与变化发布
    # ------------------------------------------------------------------
    def _collection(self, owner_id):
        return [serialize_report(row) for row in self.reports.list_reports(owner_id)]

    def _publish(self, owner_id):
        collection = self._collection(owner_id)
        with self._lock:
            listeners = tuple(self._observers.get(owner_id, ()))
        for listener in listeners:
            listener(collection)

    def _release(self, owner_id, listener):
        def stop():
            with self._lock:
                listeners = self._observers.get(owner_id)
                if listeners is not None:
                    listeners.discard(listener)
                    if not listeners:
                        self._observers.pop(owner_id, None)
        return stop
