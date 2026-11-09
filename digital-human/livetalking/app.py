###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

# server.py
from flask import Flask, render_template,send_from_directory,request, jsonify
from flask_sockets import Sockets
import base64
import json
import time
#import gevent
#from gevent import pywsgi
#from geventwebsocket.handler import WebSocketHandler
import re
import numpy as np
from threading import Thread,Event
#import multiprocessing
import torch.multiprocessing as mp

from aiohttp import web
import aiohttp
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription,RTCIceServer,RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender
from server.webrtc import HumanPlayer
from avatars.base_avatar import BaseAvatar
from llm import llm_response
import registry
from server.browser_app import setup_browser_routes
from server.microphone_stream import setup_microphone_routes
from server.routes import setup_routes
from server.rtc_manager import RTCManager
from server.session_manager import session_manager

import argparse
import random
import shutil
import asyncio
import os
import subprocess
import torch
from io import BytesIO
from threading import Timer
from typing import Dict
from utils.logger import logger
import copy
import gc


@web.middleware
async def cache_headers_middleware(request, handler):
    """Browsers may heuristically cache the HTML shell for a long time and
    then keep running stale JS. Force revalidation (still allows 304s)."""
    response = await handler(request)
    if isinstance(response, (web.Response, web.FileResponse)):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


app = Flask(__name__)
#sockets = Sockets(app)
opt = None
model = None
global_avatars = {} # avatar_id: payload
        

#####webrtc###############################
# rtc_manager replaces the old pcs set and duplicate offer handlers.
rtc_manager = None

def randN(N)->int:
    '''生成长度为 N的随机数 '''
    min = pow(10, N - 1)
    max = pow(10, N)
    return random.randint(min, max - 1)

def _setup_state_machine(sess, ranges_file="data/full_ranges.json"):
    """Attach the shared choreography state machine to a server session."""
    from state_machine import load_avatar_plan, setup_state_machine

    # 单人段表（data/avatars/<id>/ranges.json）优先：如 yy 的时间线与
    # full_ranges.json 完全不同；没有段表的人物用默认过渡表。
    plan = None
    avatar_id = getattr(sess.opt, "avatar_id", "") or ""
    if avatar_id:
        plan = load_avatar_plan(f"data/avatars/{avatar_id}/ranges.json")

    # HTTP/WebRTC input must flow immediately.  Desktop chat enables delayed
    # audio explicitly so it can synchronize speech with an action.
    setup_state_machine(sess, ranges_file=ranges_file, plan=plan, hold_audio=False)


def build_avatar_session(sessionid:str, params:dict)->BaseAvatar:
    opt_this = copy.deepcopy(opt)
    opt_this.sessionid = sessionid

    avatar_id = params.get('avatar',opt.avatar_id)
    ref_audio = params.get('refaudio','') #音色
    ref_text = params.get('reftext','')
    # 会话的 avatar_id 必须同步成实际使用的形象：状态机的段表
    # （data/avatars/<id>/ranges.json）按它查找，不同步会拿错段表
    opt_this.avatar_id = avatar_id
    # 素材黑名单（zzf1/zzf2/yy 等遗留素材）不允许通过请求参数进入会话
    from server import assets as asset_registry
    if not asset_registry.is_allowed_avatar(avatar_id):
        raise ValueError(f"人物 {avatar_id!r} 不可用（不存在或已被忽略）")
    if (avatar_id and avatar_id != opt.avatar_id):
        # Avoid reloading if already cached globally
        if avatar_id not in global_avatars:
            global_avatars[avatar_id] = load_avatar(avatar_id, frame_cache_size=opt.frame_cache_size, face_cache_size=opt.face_cache_size)
        avatar_this = global_avatars[avatar_id]
    else:
        # Default avatar loaded at startup
        avatar_this = global_avatars.get(opt.avatar_id)
    if ref_audio: #请求参数配置了参考音频
        opt_this.REF_FILE = ref_audio
        opt_this.REF_TEXT = ref_text
    custom_config=params.get('custom_config','') #动作编排配置
    if custom_config:
        opt_this.customopt = json.loads(custom_config)

    avatar_session = registry.create("avatar", opt.model, opt=opt_this, model=model, avatar=avatar_this)

    # ── 注入完整状态机：idle 循环 + 过渡 + action + talk ──
    try:
        _setup_state_machine(avatar_session)
    except Exception as e:
        logger.warning(f"Failed to setup state machine: {e}")

    return avatar_session

async def offer(request):
    return await rtc_manager.handle_offer(request)

async def on_shutdown(app):
    await rtc_manager.shutdown()


def open_browser(url: str) -> None:
    """Open the UI page once the server is listening.

    The shell's default-browser association is unreliable on some Windows
    setups (it may still point at IE), so prefer an installed Chrome/Edge
    and fall back to the system default only if neither is found.
    """
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            subprocess.Popen([exe, "--new-window", url])
            logger.info("opened %s in %s", url, exe)
            return
    import webbrowser

    if webbrowser.open(url):
        logger.info("opened %s in default browser", url)
    else:
        logger.warning("could not open a browser automatically; visit %s manually", url)



def main():
    global rtc_manager, opt, model,load_avatar
    # 解析命令行参数
    from config import parse_args
    opt = parse_args()

    # 初始化人物/音色素材库（建 data/drop 投递文件夹 + 忽略遗留素材）
    from server import assets as asset_registry
    asset_registry.init(opt)

    # ─── 加载 avatar 插件（触发 @register 注册）──────────────────────
    _avatar_modules = {
        'wav2lip':    'avatars.wav2lip_avatar',
        'musetalk':   'avatars.musetalk_avatar',    # 需自行实现
        'ultralight': 'avatars.ultralight_avatar',  # 需自行实现
    }
    import importlib

    if opt.model not in _avatar_modules:
        logger.error(f"不支持的模型: {opt.model}，可选: {list(_avatar_modules.keys())}")
        raise ValueError(f"Unsupported model: {opt.model}")

    try:
        avatar_mod = importlib.import_module(_avatar_modules[opt.model])
    except ModuleNotFoundError as e:
        logger.error(f"模型 '{opt.model}' 的模块不存在: {e}")
        logger.error(f"当前仅 'wav2lip' 模型可用。请使用 --model wav2lip")
        raise

    load_model = avatar_mod.load_model
    load_avatar = avatar_mod.load_avatar
    warm_up = avatar_mod.warm_up
    safe_options = vars(opt).copy()
    if safe_options.get("llm_api_key"):
        safe_options["llm_api_key"] = "***"
    logger.info("startup options: %s", safe_options)

    if opt.model == 'wav2lip':
        model = load_model(opt.modelfile)
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id, frame_cache_size=opt.frame_cache_size, face_cache_size=opt.face_cache_size)
        warm_up(opt.batch_size, model, 256)
    elif opt.model == 'musetalk':
        model = load_model()
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id, frame_cache_size=opt.frame_cache_size, face_cache_size=opt.face_cache_size)
        warm_up(opt.batch_size, model)
    elif opt.model == 'ultralight':
        model = load_model(opt)
        global_avatars[opt.avatar_id] = load_avatar(opt.avatar_id, frame_cache_size=opt.frame_cache_size, face_cache_size=opt.face_cache_size)
        warm_up(opt.batch_size, global_avatars[opt.avatar_id], 160)

    # init rtc manager
    session_manager.init_builder(build_avatar_session)
    rtc_manager = RTCManager(opt)
    # share avatar_sessions (RTCManager handles it but routes.py expects it)

    # Warm CosyVoice3 in the background so the first spoken reply doesn't
    # pay the whole model-load latency.
    if opt.tts == 'cosyvoice3':
        def _warm_tts() -> None:
            try:
                from tts.cosyvoice3 import warm_up as tts_warm_up

                tts_warm_up(opt.cosyvoice3_model_dir, opt)
            except Exception:
                logger.exception("CosyVoice3 warmup failed")

        Thread(target=_warm_tts, name="tts-warmup", daemon=True).start()

        # LLM 是远程 API：首条消息要付一次 TLS/认证握手（数百毫秒）。
        # 启动时就发一条 1-token 请求把连接池焐热，首句延迟只剩模型本身。
        def _warm_llm() -> None:
            try:
                from services.llm_service import LLMService

                started = time.perf_counter()
                LLMService(opt).client.chat.completions.create(
                    model=opt.llm_model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                logger.info("LLM 连接预热完成: %.2fs", time.perf_counter() - started)
            except Exception:
                logger.exception("LLM 连接预热失败（不影响对话，首条消息会自行建连）")

        Thread(target=_warm_llm, name="llm-warmup", daemon=True).start()
    
    if opt.transport in ('virtualcam', 'rtmp'):
        thread_quit = Event()
        params = {}
        # session 0 for virtualcam / rtmp
        session_manager.add_session('0', build_avatar_session('0', params))
        rendthrd = Thread(target=session_manager.get_session('0').render,args=(thread_quit,))
        rendthrd.start()

    #############################################################################
    appasync = web.Application(client_max_size=1024**2*100,
                               middlewares=[cache_headers_middleware])
    appasync["llm_response"] = llm_response

    appasync.on_shutdown.append(on_shutdown)
    appasync.router.add_post("/offer", offer)

    # The root page starts in lightweight default mode and establishes the
    # heavier WebRTC session only after the user switches modes.
    setup_browser_routes(appasync, opt)
    setup_microphone_routes(appasync, opt)

    # 注册 server/routes.py 中的通用 API 路由
    setup_routes(appasync) 

    # Configure default CORS settings.
    cors = aiohttp_cors.setup(appasync, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        })
    # Configure CORS on all routes.
    for route in list(appasync.router.routes()):
        cors.add(route)

    logger.info(
        "start unified browser app: http://<serverip>:%s/",
        opt.listenport,
    )
    logger.info(
        "default mode is shown first; use the page button to enter WebRTC lip sync"
    )
    def run_server(runner):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, '0.0.0.0', opt.listenport)
            loop.run_until_complete(site.start())
            # Give the listening socket a moment, then open the UI once.
            # Set LIVETALKING_NO_BROWSER=1 for headless / CI runs.
            if not os.environ.get("LIVETALKING_NO_BROWSER"):
                Timer(1.0, open_browser, args=(f"http://localhost:{opt.listenport}/",)).start()
            if opt.transport=='rtcpush':
                for k in range(opt.max_session):
                    push_url = opt.push_url
                    if k!=0:
                        push_url = opt.push_url+str(k)
                    loop.run_until_complete(rtc_manager.handle_rtcpush(push_url, str(k)))
            loop.run_forever()
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()
    #Thread(target=run_server, args=(web.AppRunner(appasync),)).start()
    run_server(web.AppRunner(appasync))

    #app.on_shutdown.append(on_shutdown)
    #app.router.add_post("/offer", offer)

    # print('start websocket server')
    # server = pywsgi.WSGIServer(('0.0.0.0', 8000), app, handler_class=WebSocketHandler)
    # server.serve_forever()


# os.environ['MKL_SERVICE_FORCE_INTEL'] = '1'
# os.environ['MULTIPROCESSING_METHOD'] = 'forkserver'                                                    
if __name__ == '__main__':
    try:
        mp.set_start_method('spawn')
    except RuntimeError:
        pass
    try:
        main()
    except KeyboardInterrupt:
        logger.info("server stopped")
    
    
    
