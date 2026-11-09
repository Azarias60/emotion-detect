# -*-coding: utf-8 -*-
"""前端单实例闸门：后端同一时刻只服务一个前端页面实例。

问题背景（2026-11-05）：前端是 SPA，导览页的实时语音客户端还是模块级单例，
再开一个标签页时旧页面不会自己退出——两页同时连着数字人引擎（8020），
麦克风与播报互相抢占，页面上看到的一切与后端会话也不再一一对应。

实现：一个 WebSocket 连接就是"实例占位符"。
  * 每个页面启动时连 /app-instance/ws?id=<实例id>（见前端 useSingleInstance.js）；
  * 新连接就位后，旧连接收到 {"type":"superseded"} 并以 4001 关闭；
  * 旧页面收到后整页跳到 /locked（不是 SPA 路由跳转——必须整页重载才能把
    媒体流、引擎 WebSocket、定时器这些模块级资源全部拆干净）；
  * 连接断开（关标签页/刷新/跳转）即释放槽位，只释放自己占的那一个。

进程模型：uvicorn 单进程（app_api.py 直接 uvicorn.run），内存态足够；
后端重启后所有页面会各自重连，仍是"最后连上的那个胜出"。
"""
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/app-instance", tags=["app-instance"])

# 当前占位的实例（单进程内存态）
_active_ws = None
_active_id = ""
_since = 0.0
_last_seen = 0.0

# 前端每 20s 发一次 ping；后台标签页的定时器会被浏览器节流，留足 180s 余量
_STALE_SECONDS = 180.0


def _snapshot(active: bool) -> dict:
    return {
        "active": active,
        "id": _active_id if active else "",
        "since": _since,
        "lastSeen": _last_seen,
        "staleSeconds": _STALE_SECONDS,
    }


@router.get("/status")
async def instance_status():
    """给启动器/锁定页查询：现在是否已经有一个前端实例在跑。"""
    alive = _active_ws is not None and (time.time() - _last_seen) < _STALE_SECONDS
    return _snapshot(alive)


async def _kick(old: WebSocket) -> None:
    """顶掉旧实例：先发显式消息，再带 4001 关闭（reason 部分浏览器拿不到）。"""
    try:
        await old.send_json({"type": "superseded"})
    except Exception:
        pass
    try:
        await old.close(code=4001, reason="superseded")
    except Exception:
        pass


@router.websocket("/ws")
async def instance_ws(ws: WebSocket):
    global _active_ws, _active_id, _since, _last_seen

    client_id = (ws.query_params.get("id") or "").strip()[:64] or "anonymous"
    await ws.accept()

    # 先占位再踢人：这段没有 await，单事件循环下不会被插队
    previous = _active_ws
    _active_ws = ws
    _active_id = client_id
    _since = time.time()
    _last_seen = _since

    if previous is not None and previous is not ws:
        await _kick(previous)

    try:
        await ws.send_json({"type": "active", "id": client_id, "since": _since})
        while True:
            # 只用来保活 + 更新 last_seen；内容本身没有语义
            await ws.receive_text()
            _last_seen = time.time()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # 只有"自己还是当前占位者"时才清空：避免旧连接迟到的 finally
        # 把刚接管的新实例误清掉（刷新/快速重开页面时的经典竞态）
        if _active_ws is ws:
            _active_ws = None
            _active_id = ""
            _last_seen = 0.0
        try:
            await ws.close()
        except Exception:
            pass
