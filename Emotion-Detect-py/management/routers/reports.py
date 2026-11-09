import json

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import database, utc_now
from ..dependencies import current_user
from ..responses import success
from ..schemas import ReportCreateRequest

router = APIRouter(prefix="/report", tags=["报告管理"])


def serialize_report(row, include_detail=False):
    value = {
        "id": row["id"], "type": row["report_type"], "title": row["title"],
        "summary": row["summary"], "address": row["file_address"],
        "createUserId": row["create_user_id"], "createTime": row["created_at"],
        "updateTime": row["updated_at"],
    }
    if include_detail:
        try: value["result"] = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError: value["result"] = {}
        value["rawReport"] = row["report_text"] or ""
    return value


@router.get("")
def report_list(pageNum: int = Query(1, ge=1), pageSize: int = Query(10, ge=1, le=100), type: str = "", keyword: str = "", startDate: str = "", endDate: str = "", user=Depends(current_user)):
    clauses, params = ["create_user_id=?"], [user["id"]]
    if type:
        clauses.append("report_type=?"); params.append(type)
    if keyword:
        clauses.append("(title LIKE ? OR summary LIKE ?)"); params.extend([f"%{keyword}%", f"%{keyword}%"])
    if startDate:
        clauses.append("created_at>=?"); params.append(f"{startDate}T00:00:00")
    if endDate:
        clauses.append("created_at<=?"); params.append(f"{endDate}T23:59:59+99:99")
    where = " AND ".join(clauses)
    with database() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM reports WHERE {where}", params).fetchone()[0]
        rows = connection.execute(f"SELECT * FROM reports WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [pageSize, (pageNum - 1) * pageSize]).fetchall()
    return success({"total": total, "items": [serialize_report(row) for row in rows], "pageNum": pageNum, "pageSize": pageSize})


@router.post("/save")
def save_report(request: ReportCreateRequest, user=Depends(current_user)):
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            "INSERT INTO reports(create_user_id,report_type,title,summary,result_json,report_text,file_address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (user["id"], request.type, request.title, request.summary, json.dumps(request.result, ensure_ascii=False), request.rawReport, request.fileAddress, now, now),
        )
        report_id = cursor.lastrowid
    return success({"id": report_id}, "报告保存成功")


@router.get("/detail")
def report_detail(id: int, user=Depends(current_user)):
    with database() as connection:
        row = connection.execute("SELECT * FROM reports WHERE id=? AND create_user_id=?", (id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return success(serialize_report(row, include_detail=True))


@router.delete("/delete")
def delete_report(id: int, user=Depends(current_user)):
    with database() as connection:
        cursor = connection.execute("DELETE FROM reports WHERE id=? AND create_user_id=?", (id, user["id"])).rowcount
    if not cursor:
        raise HTTPException(status_code=404, detail="报告不存在")
    return success(message="报告删除成功")

