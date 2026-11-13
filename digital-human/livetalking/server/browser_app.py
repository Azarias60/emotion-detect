"""Unified browser UI and its lightweight static default mode."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from utils.logger import logger

from server import assets, lifecycle, personas
from server.session_manager import session_manager

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
DEFAULT_ASSETS = WEB_ROOT / "default-assets"


def setup_browser_routes(app: web.Application, opt) -> None:
    """Register API routes; the legacy landing page is gone (web/ removed).

    2026-11-12：旧 LivETalking 前端页（web/）按用户要求删除——情感数字人
    走 Vite 前端（app/）。本函数现在只注册 API（/api/assets、/api/voice、
    /api/personas、/api/avatar/* 等），页面路由不再提供（根路径 404）。
    """
    if not WEB_ROOT.is_dir():
        logger.info("[Assets] web/ 已删除，仅注册 API 路由（旧前端页不再提供）")

    async def index(_request: web.Request) -> web.Response:
        page = WEB_ROOT / "index.html"
        if not page.is_file():
            return web.Response(status=404, text="LivETalking web UI removed; use the Vite app")
        return web.FileResponse(page)

    async def config(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "idleDelayMinMs": 3000,
                "idleDelayMaxMs": 7000,
                "vadThreshold": 0.035,
                "vadSilenceMs": 900,
                "fixedVoice": getattr(
                    opt, "TTS_SPK_ID", "livetalking_fixed_voice"
                ),
                "transport": getattr(opt, "transport", "webrtc"),
                "defaultTts": getattr(opt, "default_tts", "cosyvoice3"),
                "persona": personas.summary("default"),
                "personaLive": personas.summary("live"),
            }
        )

    def _mode_of(request: web.Request) -> str:
        mode = request.query.get("mode", "")
        return mode if mode in personas.MODES else ""

    async def persona_list(request: web.Request) -> web.Response:
        return web.json_response(personas.summary(_mode_of(request) or None))

    async def persona_switch(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是 JSON"}, status=400)
        persona = personas.set_active_persona(str(data.get("id", "")))
        if persona is None:
            return web.json_response({"error": "人设不存在"}, status=404)
        # 两套人设各自生效，前端两个模式都要更新
        return web.json_response(
            {
                "default": personas.summary("default"),
                "live": personas.summary("live"),
            }
        )

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "avatar": getattr(opt, "avatar_id", ""),
                "tts": getattr(opt, "tts", ""),
                "defaultAssets": all(
                    (DEFAULT_ASSETS / name).is_file() if DEFAULT_ASSETS.is_dir() else False
                    for name in ("character.png", "idle.mp4")
                ),
            }
        )

    async def readiness(_request: web.Request) -> web.Response:
        """预热进度探针：前端加载页轮询它，就绪前不放行对话输入。

        轻量页与实时页共用 CosyVoice3 单例，所以只要它没预热完，两个模式
        的第一句话都会排队等模型加载。其他 TTS 引擎没有预热阶段，直接就绪。
        """
        if "cosyvoice3" in (getattr(opt, "tts", ""), getattr(opt, "default_tts", "")):
            from tts import cosyvoice3

            tts = cosyvoice3.warm_status()
        else:
            tts = {"state": "ready", "error": ""}
        state = tts["state"]
        return web.json_response(
            {
                # failed 也放行：模型往往仍可用，别把用户锁死在加载页
                "ready": state in ("ready", "failed"),
                "tts": state,
                "error": tts.get("error", ""),
                # 本代服务的启动时间戳：页面用它识别"服务已重启"，旧页面
                # 停止心跳、bye 也被忽略，避免误伤新拉起的服务
                "epoch": lifecycle.epoch(),
            }
        )

    async def heartbeat(_request: web.Request) -> web.Response:
        # 页面存活心跳（每 5 秒一次）；响应里的 epoch 供页面做新旧服务检测
        lifecycle.mark_seen()
        return web.json_response({"ok": True, "epoch": lifecycle.epoch()})

    async def bye(request: web.Request) -> web.Response:
        # 页面关闭时的 sendBeacon。sendBeacon 无法读响应，所以这里只管记录。
        # 正文可能为空（旧版页面）或 {"epoch": ...}；epoch 不匹配的是旧代
        # 页面在补发告别，忽略，免得误杀快捷方式新拉起的服务。
        try:
            raw = await request.text()
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {}
        reported = payload.get("epoch") if isinstance(payload, dict) else None
        if reported is not None and str(reported) != str(lifecycle.epoch()):
            return web.json_response({"ok": False, "reason": "stale page"})
        lifecycle.mark_bye()
        return web.json_response({"ok": True})

    # ── 人物与音色素材库 ────────────────────────────────────────────

    async def asset_list(_request: web.Request) -> web.Response:
        return web.json_response(assets.snapshot())

    async def voice_switch(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是 JSON"}, status=400)
        voice_id = str(data.get("id", ""))
        ok, message = assets.set_active_voice(voice_id)
        if not ok:
            return web.json_response({"error": message}, status=404)
        return web.json_response({"ok": True, "message": message,
                                  "voices": assets.list_voices(),
                                  "activeVoice": assets.active_voice_id()})

    async def avatar_generate(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是 JSON"}, status=400)
        name = str(data.get("name", ""))
        ok, message = assets.start_avatar_generation(name)
        if not ok:
            return web.json_response({"error": message}, status=400)
        return web.json_response({"ok": True, "message": message})

    async def avatar_delete(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是 JSON"}, status=400)
        avatar_id = str(data.get("id", ""))
        ok, message = assets.delete_avatar(avatar_id)
        if not ok:
            return web.json_response({"error": message}, status=400)
        return web.json_response({"ok": True, "message": message,
                                  "presets": assets.list_presets(),
                                  "avatars": assets.list_avatars()})

    app.router.add_get("/", index)
    app.router.add_get("/api/default/config", config)
    app.router.add_get("/api/default/health", health)
    app.router.add_get("/api/readiness", readiness)
    app.router.add_post("/api/heartbeat", heartbeat)
    app.router.add_post("/api/bye", bye)
    app.router.add_get("/api/personas", persona_list)
    app.router.add_post("/api/persona", persona_switch)
    app.router.add_get("/api/assets", asset_list)

    async def current_segment(request):
        """当前播放片段名（idle1/greet/X_to_Y），供前端待机框展示播放逻辑。"""
        try:
            sessionid = request.query.get('sessionid', '')
            sess = session_manager.get_session(sessionid)
            if sess is None:
                # 兜底：单用户场景取最近一个存活会话（前端 sessionid 偶发不一致时仍可显示）
                live = [x for x in session_manager.sessions.values() if x is not None]
                sess = live[-1] if live else None
            if sess is None:
                return web.json_response({"code": 1, "msg": "暂无活跃会话"})
            from state_machine import TYPE_NAMES
            st = int(getattr(sess, 'custom_audiotype', 0) or 0)
            if getattr(sess, '_in_transition', False):
                tgt = int(getattr(sess, '_transition_target', 0) or 0)
                seg = "%s_to_%s" % (TYPE_NAMES.get(st, st), TYPE_NAMES.get(tgt, tgt))
                kind = "transition"
            else:
                seg = TYPE_NAMES.get(st, "state%s" % st)
                kind = "talk" if st in (8, 9) else ("idle" if st in (1, 2, 3) else "action")
            return web.json_response({"code": 0, "data": {"segment": seg, "kind": kind}})
        except Exception as e:
            return web.json_response({"code": 1, "msg": str(e)})
    app.router.add_get("/api/session/segment", current_segment)
    app.router.add_post("/api/voice", voice_switch)
    app.router.add_post("/api/avatar/generate", avatar_generate)
    app.router.add_post("/api/avatar/delete", avatar_delete)

    # 桌面快捷方式（LIVETALKING_AUTOEXIT=1）启动时：网页全关后自动退出
    lifecycle.maybe_start_watcher()

