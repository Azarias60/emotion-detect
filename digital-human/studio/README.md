# studio —— 照片→数字人生产线

把一个「段目录」变成页面里能对话的实时数字人。四道工序，都用引擎的 venv 跑。

**完整顺序**（2026-10-21 鬼子实证）：稳定化 → 质量门 → 过渡重熔 → 组装 → 身份审计。
稳定化必须走在最前——SCAIL 生成的段自带缩放漂移（实测脸宽极差 41~112%），
端点尺度失配时 LTX 过渡无论怎么调都修不好接缝（实测尖峰恒 32~34）。

## 用法

```bat
REM 第 1 道：质量门（阻断项：人脸可检 + 嘴静；参考项：背景/漂移只报告）
livetalking\venv\Scripts\python.exe studio\qa_segments.py ^
    --segments livetalking\guizi-character --out runs\qa_guizi

REM 第 2 道：组装（拼接→人脸检测→段表），产物落在 livetalking\data\avatars\<id>\
livetalking\venv\Scripts\python.exe studio\build_avatar.py ^
    --segments livetalking\guizi-character --avatar-id guizi --display-name 鬼子 --keep-video
```

跑完第三步是**注册配对**：编辑 `livetalking/data/presets.json` 加一项
（`{"id":"guizi","name":"鬼子","avatar":"guizi","voice":"default","note":"..."}`），
重启 `livetalking/run-8020.bat`，页面「数字人」芯片就多一项。

## 段目录的命名约定

- 一个 mp4 = 一个动作段，704×1280
- 基础段：`idle1 / idle2 / idle3 / greet / talk / talk2`（至少要有 idle 和 talk）
- 过渡：`idle1 to talk.mp4` 这种（脚本自动规范成 `idle1_to_talk`）
- 帧率不要求统一（20/25 都行），脚本统一补到 25fps——补帧不改变动作真实速度

## 两道工序各自查什么

**qa_segments.py**（每个基础段）：
- 人脸可检（阻断）——insightface 检出率 ≥98%，genavatar 的前提
- 嘴静（阻断）——按真实脸盒算嘴/额差异比 ≤2.5；第一原则要求全链路闭嘴，
  嘴由 wav2lip 用音频实时驱动（见 `../../docs/photo-to-digital-human-pipeline.md`）
- 背景 / 漂移（参考）——真人是实拍背景、动作段本来就会动，只报告不判死

**build_avatar.py**：
- 38 段拼接成一条 25fps 视频（基础段在前，过渡按名排序）
- 调引擎 `genavatar_stream.py` 做人脸检测 + 256 对齐裁剪 → full_imgs / face_imgs / coords.pkl
- 按各段时长算帧偏移写 `ranges.json`（状态机段表；实际抽帧数与预算不一致时自动修正）
- 打印每个基础段的帧数/时长，供核对

## 产物校验

```bat
livetalking\venv\Scripts\python.exe -c "import json,pathlib; d=json.load(open('livetalking/data/avatars/guizi/ranges.json')); print(len([k for k in d if not k.startswith('_')]),'段 | _total',d['_total'])"
dir livetalking\data\avatars\guizi\full_imgs | find /c ".png"
```

full_imgs / face_imgs / coords.pkl 三个数量应与 `_total` 一致。

## 第 0 道 · 几何稳定化（`stabilize_segments.py`，必须在最前）

SCAIL-2 动作迁移会引入模型固有的缩放漂移（驱动视频本身只漂 3~7%，生成段漂 41~112%）。
不修它，段间端点尺度失配，LTX 过渡只能生成"静态折中帧"（接缝爆跳+中段僵死）。

```bat
livetalkingenv\Scripts\python.exe studio\stabilize_segments.py ^
    --segments livetalking\guizi-character --out runs\guizi_stab --passes 2
```

- 测量源是 insightface 脸盒（模板匹配会低估漂移；脚底锚定在暗背景下失效）
- 全局统一目标 + 二轮迭代；验收：稳后脸宽极差 <5%、端点一致性 <5%
- **端点截断规则**：段尾若是大幅降采样的近景帧（warp<0.85），LTX 无法插值——
  把段截到 warp≈1 的帧再熔过渡（实测尖峰 34→6）
- 跑完先备份原段再覆盖：`for %f in (idle1 idle2 idle3 greet talk talk2) do copy ...`

## 第 3.5 道 · 身份审计（`audit_identity.py`，过渡重熔后必跑）

防止"人物时不时换脸"——过渡段最容易串成别人的素材（guizi 项目实测 30 条过渡全是 zzf）。

```bat
set G=idle1+idle2+idle3+greet+talk+talk2
livetalkingenv\Scripts\python.exe studioudit_identity.py ^
    --segments livetalking\guizi-character --expect %G% --refs %G%,my_avatar,yy
```

- 每段采帧 → insightface 脸盒 → 与参考均值脸算归一化互相关，输出最像谁 + margin
- **参考脸必须用基础段**（`--refs idle1+idle2+...`），不能拿 `data/avatars/<id>/full_imgs`
  ——那是历史拼接产物，可能混着别人的过渡段（拿它当参考会把别人当成本人）
- 合格线：全部段最像本人且 margin >0.3

## 辅助 · 亮度兜底（`fix_brightness.py`，正常情况下不用）

配方修不掉中段亮度差时的第二道保险：对中段做逐通道均值/标准差线性匹配。
触发条件是**段内** guide 帧与中段的亮度差 >4（拿外部参考去"修"内部一致的素材会修出 pop）。

## 过渡重熔（`transition_queue.py`）

```bat
livetalkingenv\Scripts\python.exe studio	ransition_queue.py submit ^
    --pairs "idle1 to talk" "talk to idle1" ... --run guizi_t8 [--frames 81]
livetalkingenv\Scripts\python.exe studio	ransition_queue.py collect --run guizi_t8
```

- submit/collect 分离：单段生成约 2.5 分钟（首段含模型加载约 35 分钟），collect 顺带打印
  帧差尖峰和首/中段亮度差（⚠标记）
- **尖峰判据**：<15 可用（已上线 3D 库基准 6.25~20.24）；>25 的配对先查 guide 对差异
  （A 末帧 vs B 首帧像素差 >25 就要回稳定化/截断，或加 --frames 81 摊薄形变）
- 队列取消：`POST /interrupt` + `POST /queue {"delete":[ids]}`
