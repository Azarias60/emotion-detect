import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from ..database import database, utc_now
from ..dependencies import current_user
from ..responses import success
from ..schemas import AnalysisRequest

router = APIRouter(prefix="/report", tags=["历史分析"])
TYPE_LABELS = {"health": "健康问诊", "tongue": "舌象检测", "fatigue": "疲劳检测", "emotion": "情绪识别", "companion": "数字人对话", "comprehensive": "综合分析"}


@router.post("/analysis")
def analyze_reports(request: AnalysisRequest, user=Depends(current_user)):
    clauses, params = ["create_user_id=?", "report_type<>'comprehensive'"], [user["id"]]
    if request.startDate:
        clauses.append("created_at>=?"); params.append(f"{request.startDate.isoformat()}T00:00:00")
    if request.endDate:
        clauses.append("created_at<=?"); params.append(f"{request.endDate.isoformat()}T23:59:59+99:99")
    with database() as connection:
        rows = connection.execute(f"SELECT * FROM reports WHERE {' AND '.join(clauses)} ORDER BY created_at ASC", params).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="选定日期内没有可分析的报告")
        counts = Counter(row["report_type"] for row in rows)
        recent = rows[-5:]
        report_lines = [f"共分析 {len(rows)} 份历史报告。"]
        report_lines.append("检测构成：" + "、".join(f"{TYPE_LABELS.get(key,key)} {value} 次" for key, value in counts.items()) + "。")
        summaries = [row["summary"] for row in recent if row["summary"]]
        if summaries:
            report_lines.append("近期摘要：" + "；".join(summaries) + "。")
        report_lines.append("建议结合多次检测趋势观察变化；本汇总仅供健康参考，不能替代专业诊断。")
        analysis_text = "\n\n".join(report_lines)
        now = utc_now()
        result = {"reportCount": len(rows), "typeCounts": dict(counts), "startDate": str(request.startDate or ""), "endDate": str(request.endDate or ""), "recentReportIds": [row["id"] for row in recent]}
        cursor = connection.execute(
            "INSERT INTO reports(create_user_id,report_type,title,summary,result_json,report_text,file_address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (user["id"], "comprehensive", "历史检测综合分析", f"已汇总 {len(rows)} 份检测报告", json.dumps(result, ensure_ascii=False), analysis_text, "", now, now),
        )
    return success({"id": cursor.lastrowid, "analysis": analysis_text, **result}, "分析成功")

