# LiveTalking 数字人 — 问题排查结论与修改建议（交接文档）

日期：2026-09-10 ｜ 排查方式：运行日志 + 代码审读 + WebSocket 复现 + 浏览器视觉验收
当前服务状态：8010 端口运行中，但处于 **P0-A 故障状态**（完全无声 + 界面卡死）。改完代码必须重启服务才生效。

> **2026-09-17 追加**：下面 P0-A / P0-B / P1 的结论已被后续工作覆盖，本文档保留作历史记录。
> 新一轮修复（实时口型模式"回复重复 / 一直在说 / 互相重叠"+ 提速）见
> `E:\ZCodeWorkspace\livetalking-live-reply-fix\修复说明.md`。要点：
> ① 回复代际改为在**回复开始时**锁定（`BaseAvatar.begin_reply()`），旧回复的迟到段落不再被盖上最新代际；
> ② live 模式加 `live_reply_id` 串行闸门（`_begin_live_reply()` 返回 `should_stop`）；
> ③ `POST /human` 的 `type=chat` 一律取代上一轮（实时口型页文字输入不带 `interrupt`，这是"重叠"的真实入口）；
> ④ live 模式的回声（ghost）防护此前从未生效（`reply_text` / `last_reply_audio_at` 从未赋值）→ 数字人自问自答；
> ⑤ yy 段表缺动作段时 `set_state` 返回 `False` 静默降级，不再每 delta 刷 `ValueError` 堆栈；
> ⑥ 提速：`res_frame_queue` 深度减半、OpenAI 客户端单例、`[Latency]` 首音延迟分解日志、去掉重复的 `notify()`。
> 回归测试：`venv\Scripts\python.exe -m unittest discover -s tests` → **147 passed**。

---

## P0-A｜TTS 完全无声 + 界面永久 busy（当前线上故障）

**现象**（浏览器实测）：发送「讲个笑话」→ 回复只流出第一段「好呀，」→ 字幕停更、右上角「思考中」常驻、输入禁用，busy 状态 21 秒以上不解除，无任何音频。WebSocket 层实测：20 秒无音频帧、无 `response_done`。

**根因**：`tts/cosyvoice3.py` 的 `_generate()` 和 `warm_up()`（今天 10:30 的改动，原意修 P0-B）把推理从 `inference_zero_shot` 改成了 `inference_cross_lingual`。但 CosyVoice3 的 LLM 有硬断言（`cosyvoice_repo/cosyvoice/llm/llm.py:479`）：token 序列必须含 `<|endofprompt|>`（token 151646）。cross-lingual 路径（`frontend.py frontend_cross_lingual`）会把 `prompt_text` 从输入中删掉，而合成文本本身不含该 token → `llm_job` 线程 `AssertionError` 崩溃 → 上游 `model.py tts()` 的流式循环里 `llm_end_dict` 永远不置 True → **无限轮询挂死** → `_generate` 不返回 → `server/microphone_stream.py _stream_default_reply` 卡在 `speak()` → 本轮 generation 永不结束。

**日志证据**：`E:\ZCodeWorkspace\scratch\livetalking\server_run.log` 第 26-30 行起，`Exception in thread Thread-2/3 (llm_job)` + `AssertionError: <|endofprompt|> not detected`。

**修复**：
1. 回滚两处 cross-lingual 改动，恢复 `model.inference_zero_shot(text, "", "", zero_shot_spk_id=speaker_id, stream=True, speed=self.speed)`（`warm_up` 里同理，保持与生产路径一致）。
2. **加固（强烈建议）**：上游 `tts()` 对 `llm_job` 崩溃零防护（线程死了主循环永远等）。不要改 repo；在 `tts/cosyvoice3.py _generate` 外加看门狗——迭代流式输出时若首块超过约 10 秒无产出，break 并抛 `RuntimeError`，让上层现有的重试/Windows 兜底逻辑接手。这样任何上游异常都不会再挂死整条回复。

---

## P0-B｜短句合成「幻觉续写」→ 语音拖长 + 乱音（用户最初报的「乱码/音色不对」）

**现象（实测数据）**：
- 4 字片段「你好呀，」→ 3.16 秒音频（0.79 秒/字；正常约 0.21）
- 一句 39 字回复 → 28.5 秒音频（0.73 秒/字）
- 波形分析显示：念完正文后还有约 2 秒**真实语音**的幻觉内容（中间有停顿再续念）——这就是用户听到的「乱码」和「音色不对」（拖长变形后音色面目全非）

**根因**：zero-shot 模式本质是「续写」音色注册样本的语音。轻量页小男孩音色（`livetalking_default_boy`）注册 prompt 文本 35 字，而服务端按句切分的**首段只有 3-4 字**（`microphone_stream.py FIRST_SEGMENT_MIN=3`），比例 0.11，远低于 CosyVoice 自身要求的 ≥0.5（`cosyvoice.py:96` 有对应 warning「too short than prompt text … bad performance」，但走 spk_id + 空 prompt_text 时不会触发）。LLM 找不到结束点就继续编。实时口型的固定音色（注册文本 22 字）同理，只是切句更大（首段≥4、后续 8-36 字）症状较轻。

**修复（三管齐下，回滚 P0-A 之后做）**：
1. **重新注册短样本音色**：`tts/cosyvoice3.py _ensure_default_page_speaker` 的 `sample_text` 从 35 字长句换成 ≤8 字短句（如「你好呀，我是暖暖。」），并把缓存 ID 换新名（如 `livetalking_default_boy_v2`）强制重新生成注册。注册文本越短，比例问题越轻。
2. **提高首段下限**：`server/microphone_stream.py` 的 `FIRST_SEGMENT_MIN` 3→6；`llm.py` 的 `FIRST_SEGMENT_MIN` 4→8（live 模式同理）。目标是每段长度 ≥ 注册文本的一半。首包延迟会略增（可接受），换来整段不乱。
3. **尾部修剪（保险）**：对每段 `_generate` 输出做尾部 trim（连续低于阈值幅度超 ~0.3 秒即截断）。幻觉语音是真实语音、trim 不掉，但 1+2 修根之后残留的多余静音可以去掉，观感更好。

**不要尝试**：cross-lingual（见 P0-A 教训）；给 tts_text 拼 `<|endofprompt|>` 前缀（text_frontend 归一化路径未验证，高风险）。

---

## P1｜提速（用户明确要求）

实测基线：文字发送 → 首块音频 1.9-2.0 秒 ≈ LLM 首块 0.3-0.8s + CosyVoice3 首包 1.2-1.6s（日志波动 1.8-3.3s）。

建议（按收益排序）：
1. P0-B 修复本身就是最大提速：幻觉 token 消失后，短段合成耗时约减半（2.2s → ~1s）。
2. `FIRST_SEGMENT_MIN` 提到 6 后首段一次合成更多字，减少「首段念完等第二段」的可感知停顿。
3. `services/llm_service.py` 每轮对话都新建 `OpenAI` 客户端；改为进程级单例可省握手开销。
4. 低优先级：Whisper 换 faster-whisper（`--asr_backend` 已预留参数）——当前 ASR 稳态 <1s 不是瓶颈。

**验收指标**：文字→首块音频 ≤1.5s；音频时长/字数 ≤0.3s/字。

---

## P2｜小问题

1. **web/app.js**：Enter 提交偶发不触发（手动 dispatch `submit` 事件正常；复现条件未定位）。建议给输入框显式绑定 `keydown` Enter。窄视口下「发送」按钮在视口外（x≈1124）、大标题「你好，我在听」被截断、人设 chip 文字挤压——建议布局加 max-width/换行保护。
2. **日志缺口**：默认页对话（`_stream_default_reply`）不打 transcript、LLM 首块耗时、分段合成耗时，排查全靠 TTS 行猜。建议补 INFO 日志。
3. **显存互杀**：服务运行时另起进程加载 CosyVoice（如测试脚本）会导致 OOM 互杀（已实际发生：venv 服务被测试进程挤掉，之后有人用 `D:\Anaconda\python.exe` 裸启又占了 8010）。约定：验证一律连服务进程走 WS，不要独立加载模型；或启动时加显存预检。

---

## 给下一个 agent 的复现/验证工具（均已存在）

| 脚本 | 用途 | 通过标准 |
|---|---|---|
| `E:\ZCodeWorkspace\scratch\livetalking\check_ws_default.py` | 连服务 WS 发两轮对话，收全量 JSON+PCM | 文本完整、音频 ≤0.3s/字、收到 `response_done`、总耗时 <15s |
| `E:\ZCodeWorkspace\scratch\livetalking\check_hang.py` | 挂死检测 | 20 秒内出音频即通过 |
| `E:\ZCodeWorkspace\scratch\livetalking\check_stretch.py` | 全句 vs 分段合成对比 | 分段合计时长与全句一致（±20%） |

回归测试（不加载模型）：`venv\Scripts\python.exe -m unittest discover -s tests`（2026-09-10 时为 49/49；**2026-09-17 已扩到 147/147 通过**）。

**启动命令**：`cd E:\LiveTalking\LiveTalking && venv\Scripts\python.exe app.py --transport webrtc --model wav2lip --avatar_id my_avatar --tts cosyvoice3`（勿用系统 Python）。

## 涉及文件

- `tts/cosyvoice3.py` — 回滚 cross-lingual、短注册样本、尾部 trim、看门狗
- `server/microphone_stream.py` — `FIRST_SEGMENT_MIN` 3→6
- `llm.py` — `FIRST_SEGMENT_MIN` 4→8
- `services/llm_service.py` — 客户端单例
- `web/app.js` / `web/app.css` — Enter 绑定、窄视口布局
- 参考（勿改）：`cosyvoice_repo/cosyvoice/cli/model.py`（tts 流式循环无超时）、`llm/llm.py:479`（endofprompt 断言）、`cli/cosyvoice.py:96`（短文本 warning）
