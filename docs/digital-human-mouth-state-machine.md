# 数字人口型状态机方案（段库 + 过渡 + 说话驱动）

> 参考实现：`E:\LiveTalking\LiveTalking\state_machine.py`（段表状态机）与
> `avatars\wav2lip_avatar.py`（音频特征实时驱动口型）。本文是 emotionDetect
> 数字人段库的落地方案：2026-10-16 初版。

## 1. 段库（状态）

| 状态 | 段 | 帧数 | 口型 | 说明 |
|---|---|---|---|---|
| idle1/2/3 | 3D_idle1/2/3.mp4 | 77/37/57 | 静口 | 待机循环，随机轮换 |
| talk | 3D_talk.mp4 | 49 | 张嘴说话 | 说话循环 |
| greet | 3D_greet.mp4 | 57 | 张嘴+挥手 | 仅首次说话触发一次 |

## 2. 过渡边（20 条，57帧=2.85s，LTX 首尾帧）

**张嘴组（5）**——进入/延续说话，过渡中口型就在动：
`greet→talk`、`talk→talk`、`idle1/2/3→talk`

**静嘴组（15）**——非说话切换，口型闭合：
`talk→idle1/2/3`、`idle1/2/3→greet`、`greet→idle1/2/3`、`idle↔idle`（6）

张嘴组的提示词在 v34 位置锁定配方基础上把 `lips softly closed` 换成
`his mouth opens and closes gently and rhythmically, as if he is naturally talking`
并移除负向里的 talking/mouth opening 等词（LTX cfg=2.5，提示词可咬合；
SCAIL cfg=1 提示词惰化，段的口型靠 seed 遴选 + GLM 验证）。

## 3. 说话驱动流程（用户示例）

```
首次说话「你好」：
  greet →(greet→talk)→ talk →(talk→talk)×N →(talk→idle)→ idle 轮换
后续说话：
  idle →(idle→talk)→ talk →(talk→talk)×N →(talk→idle)→ idle 轮换
```

- greet 只触发一次：`speech_ever_active` 标志（LiveTalking 同款）。
- 说话开始（ASR/VAD 检测到人声）→ `go_talk_if_speaking()`：若当前在 idle，立即 idle→talk。
- 说话中某段播完 → 进 talk 循环（`talk→talk`），不切回 idle。
- 说话结束 → `talk→idle`，回待机轮换。
- 待机中随机轮换 idle1/2/3（`pick_idle`，排除当前段）。

## 4. 段表格式（供前端/播放器集成）

把 5 段 + 20 过渡按需拼接为单一视频，记录帧区间（LiveTalking `zzf_full_ranges.json` 格式）：

```json
{
  "idle1":  {"range": [0, 76]},
  "idle2":  {"range": [77, 113]},
  "idle3":  {"range": [114, 170]},
  "talk":   {"range": [171, 219]},
  "greet":  {"range": [220, 276]},
  "greet_to_talk":  {"range": [277, 333]},
  "talk_to_talk":   {"range": [334, 390]},
  "idle1_to_talk":  {"range": [391, 447]},
  "talk_to_idle1":  {"range": [448, 504]},
  "_total": 505
}
```

状态机按 (from,to) 查过渡区间；缺边时直切（LiveTalking 的降级策略）。
过渡泛化：段表可只写一条 `idle1→talk`，同源同向复制到 idle2/3。

## 5. 实时口型（下一步升级方向）

现状：talk/greet 的口型是生成时"抽奖"出来的固定开合节奏（seed 遴选+GLM 验证），
不与实际语音同步。LiveTalking 的做法是 wav2lip 式：TTS 音频提 Mel 特征，
实时驱动嘴部重渲染（`inference_batch(audiofeat_batch, img_batch)`）。
升级路径：保留段库管身体/姿态切换，talk 状态下用音频特征驱动口型，
即可做到"说到哪个字嘴动到哪个字"，且一句话任意长度都能配。

## 6. 已知待决事项

- **水印**：五段均带录制 App 的「AI 生成」水印（源视频带入，右下角色值 ~58 vs 纯黑 12）。
  选项：接受 / 裁底边 / 局部遮盖（遮盖属图像后处理，用户曾否决调色类后处理）。
- **过渡背景偏暗**：LTX 过渡四角色值 ~7.6，段本身 ~12，接缝处有轻微色阶差
  （历史轮次同样存在，未做色彩后处理）。
- **idle3 抱臂**：源视频本身有抱臂动作，SCAIL 忠实还原；如需完全静止的 idle3 需重录源。
