###############################################################################
#  服务器路由 — 统一异常处理的 API 路由
###############################################################################

import json
import numpy as np
import asyncio
from aiohttp import web

from utils.logger import logger


# ─── 路由工具函数 ──────────────────────────────────────────────────────────

def json_ok(data=None):
    """返回成功 JSON 响应"""
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return web.Response(
        content_type="application/json",
        text=json.dumps(body),
    )


def json_error(msg: str, code: int = -1):
    """返回错误 JSON 响应"""
    return web.Response(
        content_type="application/json",
        text=json.dumps({"code": code, "msg": str(msg)}),
    )


from server.session_manager import session_manager

def get_session(request, sessionid: str):
    """从 app 中获取 session 实例"""
    return session_manager.get_session(sessionid)


# ─── 路由处理函数 ──────────────────────────────────────────────────────────

async def human(request):
    """文本输入（echo/chat 模式），支持 voice/emotion 参数"""
    try:
        params: dict = await request.json()

        sessionid: str = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")

        is_chat = params.get('type') == 'chat'
        should_stop = None

        # chat 是"一轮新的对话"，必须取代上一轮。实时口型页的文字输入
        # （web/app.js 的 liveForm）发的就是 chat 且不带 interrupt；若照原样
        # 派发，上一轮还在飞的 llm_response 会和新一轮同时往同一条 TTS 队列
        # 灌段落 —— 听感正是"回复重复、一直在说、互相重叠"。
        # echo 只是把一段文字念出来，保持可排队语义，只有显式 interrupt 才打断。
        if params.get('interrupt') or is_chat:
            begin_turn = getattr(avatar_session, 'begin_new_turn', None)
            if begin_turn is not None:
                try:
                    # 抬回复代际 + 打断在播语音，并拿到"被取代即停手"的信号
                    should_stop = begin_turn()
                except Exception:
                    logger.exception('human route: 打断上一轮回复失败')
            if should_stop is None:
                avatar_session.flush_talk()
            clear_buffer = getattr(getattr(avatar_session, 'output', None), 'clear_buffer', None)
            if clear_buffer is not None:
                clear_buffer()

        datainfo = {}
        if params.get('tts'):  # tts 参数透传（voice, emotion 等）
            datainfo['tts'] = params.get('tts')

        if params['type'] == 'echo':
            avatar_session.put_msg_txt(params['text'], datainfo)
        elif is_chat:
            llm_response = request.app.get("llm_response")
            if llm_response:
                text = params['text']
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: llm_response(
                        text, avatar_session, datainfo,
                        should_stop=should_stop,
                    ),
                )

        return json_ok()
    except Exception as e:
        logger.exception('human route exception:')
        return json_error(str(e))


async def dance(request):
    """让当前数字人跳一段舞（段表里的 dance 段，播完自动过渡回待机）。

    点击后页面在画布上显示加载动画，本接口在后台同步预加载舞蹈帧
    （约 1~2 秒）再切换画面，返回即表示舞蹈已经开始。
    """
    try:
        params: dict = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        action_ranges = getattr(avatar_session, 'action_ranges', None)
        if not action_ranges or 10 not in action_ranges:
            return json_error("当前人物没有舞蹈段")
        activate = getattr(avatar_session, '_activate_state', None)
        preload = getattr(avatar_session, '_preload_state', None)
        if activate is None or preload is None:
            return json_error("当前会话不支持动作编排")
        # 正在跳舞（或正在切入舞蹈）时不再重复触发
        current = getattr(avatar_session, '_current_state', None)
        target = getattr(avatar_session, '_transition_target', None)
        if current == 10 or (getattr(avatar_session, '_in_transition', False)
                             and target == 10):
            return json_ok(data={"status": "already_dancing"})

        def _start_dance():
            # 同步预加载（含人脸帧）：页面加载动画盖住这段时间，
            # 切过去时不会因为读盘掉帧
            preload(10, background=False)
            activate(10)

        await asyncio.get_event_loop().run_in_executor(None, _start_dance)
        return json_ok(data={"status": "dancing"})
    except Exception as e:
        logger.exception('dance route exception:')
        return json_error(str(e))


async def interrupt_talk(request):
    """打断当前说话"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.flush_talk()
        clear_buffer = getattr(getattr(avatar_session, 'output', None), 'clear_buffer', None)
        if clear_buffer is not None:
            clear_buffer()
        return json_ok()
    except Exception as e:
        logger.exception('interrupt_talk exception:')
        return json_error(str(e))


async def humanaudio(request):
    """上传音频文件"""
    try:
        form = await request.post()
        sessionid = str(form.get('sessionid', ''))
        fileobj = form["file"]
        filebytes = fileobj.file.read()

        datainfo = {}

        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.put_audio_file(filebytes, datainfo)
        return json_ok()
    except Exception as e:
        logger.exception('humanaudio exception:')
        return json_error(str(e))


async def set_audiotype(request):
    """设置自定义状态（动作编排）"""
    try:
        params = await request.json()
        sessionid = params.get('sessionid', '')
        avatar_session = get_session(request, sessionid)
        if avatar_session is None:
            return json_error("session not found")
        avatar_session.set_custom_state(params['audiotype'])
        return json_ok()
    except Exception as e:
        logger.exception('set_audiotype exception:')
        return json_error(str(e))


async def is_speaking(request):
    """查询是否正在说话"""
    params = await request.json()
    sessionid = params.get('sessionid', '')
    avatar_session = get_session(request, sessionid)
    if avatar_session is None:
        return json_error("session not found")
    return json_ok(data=avatar_session.is_speaking())


# ─── 路由注册 ──────────────────────────────────────────────────────────────

def setup_routes(app):
    """注册所有路由到 aiohttp app"""
    app.router.add_post("/human", human)
    app.router.add_post("/humanaudio", humanaudio)
    app.router.add_post("/set_audiotype", set_audiotype)
    app.router.add_post("/dance", dance)
    app.router.add_post("/interrupt_talk", interrupt_talk)
    app.router.add_post("/is_speaking", is_speaking)
    app.router.add_static('/', path='web')
