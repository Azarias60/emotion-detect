"""检测 API 的统一成功响应；保留现有 msg 字段。"""
def success(data=None, msg="成功"):
    return {"code": 0, "msg": msg, "data": data}
