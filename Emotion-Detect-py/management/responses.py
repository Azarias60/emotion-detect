def success(data=None, message="操作成功"):
    return {"code": 0, "message": message, "data": data}


def failure(message, data=None):
    return {"code": 1, "message": message, "data": data}

