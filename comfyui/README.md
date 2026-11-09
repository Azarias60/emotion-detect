# ComfyUI 工作流合集使用说明

> 本说明依据当前目录中的压缩包、模型、工作流 JSON、说明文本和本机硬件实际检查后编写。

## 1. 这套文件是什么

这里不是已经解压、可以直接启动的 ComfyUI 目录，而是一套 Windows 版 ComfyUI 素材包：

- `No1`：ComfyUI 便携整合包和 Z-Image Turbo 文生图基础环境；
- `No2`：Qwen Image Edit 2511 图生图/图片编辑；
- `No3`：Wan 2.2 文生视频、图生视频；
- `No4`：Anima 二次元文生图；
- `No5`：LTX 2.3 文生图、图生图、首尾帧视频；
- `No6`：Z-Image 动作/姿态迁移和 Qwen-VL 提示词反推；
- `No7`：SeedVR2 图片批量超分和视频超分；
- `No8`：Anima 可视化标签和 ControlNet；
- `No09`：SCAIL-2 视频动作迁移、视频人物替换；
- `No10`：LTX 2.3 音频驱动视频；
- `No11`：Qwen Image Edit 局部重绘、姿态控制和局部替换。

本机检查结果：

- 显卡：NVIDIA GeForce RTX 4080 SUPER；
- 显存：16GB；
- 内存：约 64GB；
- Git：已安装，版本 `2.45.2.windows.1`；
- `ComfyUIportable.7z`：已通过完整性测试，压缩包正常。

因此，本说明按“16GB 显存高配版”编写。视频生成仍建议先从 832×480 或 960×544、短帧数开始，测试稳定后再增加分辨率或时长。

## 2. 最快上手流程

### 第一步：解压基础整合包

用 7-Zip 解压：

```text
No1基础包含文生图模型\ComfyUIportable.7z
```

解压后应得到类似结构：

```text
ComfyUIportable\
├── ComfyUI\
├── python_embeded\
├── update\
└── nvidia_gpu一键启动.bat
```

不要把压缩包里的 `ComfyUIportable` 再套进多个同名文件夹。最终应能直接看到 `nvidia_gpu一键启动.bat`。

### 第二步：先启动一次

双击：

```text
ComfyUIportable\nvidia_gpu一键启动.bat
```

看到命令窗口给出地址后，用浏览器打开：

```text
http://127.0.0.1:8188
```

不要关闭命令窗口；关闭它就会停止 ComfyUI。

第一次只建议验证界面可以打开。确认无错误后关闭 ComfyUI，再复制后续模型和节点。

### 第三步：合并模型和自定义节点

目标 ComfyUI 根目录是：

```text
ComfyUIportable\ComfyUI
```

复制时要“合并同名目录”，不要把整个 `NoX...` 文件夹放进 `models`。

| 来源 | 复制到的位置 | 说明 |
|---|---|---|
| `No2图生图模型\10-16G显存下载\` 下的 `loras`、`text_encoders`、`unet`、`vae` | `ComfyUIportable\ComfyUI\models\` | No2 没有外层 `models`，四个目录直接合并进去 |
| `No3文图生视频\8G及以上显存高配电脑下载\models\` | `ComfyUIportable\ComfyUI\models\` | 当前保留的是 Q4 高配版 |
| `No4二次元动漫文生图\models\` | `ComfyUIportable\ComfyUI\models\` | Anima 基础模型 |
| `No5首尾帧视频LTX23\models\` | `ComfyUIportable\ComfyUI\models\` | LTX 2.3 GGUF 和 VAE |
| `No6动作迁移及提示词反推\models\` | `ComfyUIportable\ComfyUI\models\` | ControlNet patch 和 Qwen-VL |
| `No6动作迁移及提示词反推\custom_nodes\` | `ComfyUIportable\ComfyUI\custom_nodes\` | 主要补充预处理器模型到 `ckpts` |
| `No7图片批量超分放大\models\` | `ComfyUIportable\ComfyUI\models\` | SeedVR2 模型 |
| `No8二次元可视化标签控制\models\` | `ComfyUIportable\ComfyUI\models\` | Anima LLLite 模型 |
| `No8二次元可视化标签控制\custom_nodes\` | `ComfyUIportable\ComfyUI\custom_nodes\` | Anima 标签选择器和 LLLite 节点 |
| `No09视频动作迁移人物替换\8G以上显存推荐下载\models\` | `ComfyUIportable\ComfyUI\models\` | 16GB 显卡使用 int8 convrot 版 |
| `No10音频驱动生成视频\models\` | `ComfyUIportable\ComfyUI\models\` | LTX 2.3 音频驱动 int8 版 |
| `No11局部重绘替换万物\models\` | `ComfyUIportable\ComfyUI\models\` | 只含新增 LoRA；必须先安装 No2 |

如果 Windows 提示存在同名模型：

- 文件名和大小都相同，可跳过重复复制；
- 不要随意重命名模型，工作流按固定文件名寻找模型；
- `LTX23_audio_vae_bf16.safetensors` 在 `checkpoints` 和 `vae` 中各有用途，不要只因同名就删除其中一个目录里的文件。

### 第四步：加载工作流

最简单的方法是把所需 `工作流\*.json` 直接拖进 ComfyUI 浏览器画布。

若希望工作流长期出现在菜单中，把 JSON 复制到：

```text
ComfyUIportable\ComfyUI\user\default\workflows\
```

基础整合包中已经内置了部分 No1–No09 工作流，但仍建议优先拖入各 `NoX\工作流` 目录里的 JSON，以使用下载包中的对应版本。No10 和 No11 必须使用各自目录里的新工作流。

### 第五步：检查红色节点并运行

加载工作流后：

1. 如果出现红色节点，打开右上角 Manager；
2. 选择 `Install Missing Custom Nodes` 或 `Install Missing Nodes`；
3. 安装完成后重启 ComfyUI；
4. 检查模型加载器下拉框是否已选中正确文件；
5. 替换示例图片、视频或音频；
6. 修改提示词和必要参数；
7. 点击 `Queue`/“运行”按钮，或按 `Ctrl+Enter`。

整合包已经包含常用节点，例如 ComfyUI-GGUF、KJNodes、VideoHelperSuite、rgthree、Easy-Use、Inspire、ControlNet Aux、QwenVL、RMBG、SeedVR2 和 Anima 节点。通常只需在版本不匹配时通过 Manager 补装。

生成结果默认在：

```text
ComfyUIportable\ComfyUI\output\
```

## 3. 每个工作流怎么用

### No1：Z-Image Turbo 文生图

工作流：`Z-Image Turbo文生图 (低显存).json`

1. 在正向提示词节点输入想生成的画面；
2. 设置宽、高、随机种子等参数；
3. 点击运行；
4. 默认关闭的 Mystic LoRA 会影响成人内容生成，不需要时保持关闭；选中 LoRA 节点后按 `Ctrl+B` 可切换启用/旁路状态。

No1 的基础模型已经放在 `ComfyUIportable.7z` 内，不需要再复制一套。

### No2：Qwen Image Edit 图生图

工作流：`▶QwenImageEdit2511-AIO图生图12G.json`

1. 在“加载图像”节点选择待编辑图片；
2. 用自然语言说明修改内容，例如“把背景换成夜晚城市”“把衣服改成黑色夹克”；
3. 第二张参考图默认关闭，需要多图参考时选中相应节点并按 `Ctrl+B`；
4. 默认按输入图比例输出；需要手动指定尺寸时，按工作流注释启用尺寸节点并把对应 Latent 接到 KSampler；
5. 运行并查看保存结果。

No11 依赖 No2 的 Qwen 主模型、文本编码器和 VAE，因此先复制 No2，再复制 No11。

### No3：Wan 2.2 文生视频/图生视频

工作流：

- `wan2.2-t2v-rapid-aio-gguf-文生视频.json`：文字生成视频；
- `wan2.2-i2v-rapid-aio-gguf-图生视频.json`：图片生成视频。

推荐首次参数：

- 分辨率：832×480 或 960×544；竖屏时交换宽高；
- 帧数：不超过 81；
- 帧率：16；
- 批量：1。

时长计算：

```text
视频秒数 =（总帧数 - 1）÷ 帧率
```

例如 81 帧、16 FPS 约为 5 秒。先短视频试跑，确认动作和构图后再增加成本。

### No4：Anima 二次元文生图

工作流：`Anima Base V1.0 文生图.json`

1. 输入人物、服装、场景、画风和构图提示词；
2. 推荐分辨率：竖屏 832×1216，横屏 1216×832，方图 1024×1024；
3. 保留默认 Anima Turbo LoRA 先测试；
4. 点击运行。

### No5：LTX 2.3 文生图、图生图和首尾帧视频

工作流：

- `LTX-2.3_文生图_图生图_GGUF.json`；
- `LTX-2.3首尾帧GGUF.json`。

文生图/图生图工作流中：

- 开关为 `true`：文生图，但仍需上传一张占位图片；
- 开关为 `false`：图生图，上传的图片作为参考。

首尾帧工作流中分别上传第一帧和最后一帧，再描述中间发生的动作。建议从 832×480、24 FPS、短时长开始。

提示词应重点描述“动作如何随时间变化”，也可以描述环境声、音效或对白。使用参考图时，不必重复堆砌图中已经存在的静态细节。

### No6：动作迁移和提示词反推

工作流：`z-image_turbo_controlnet动作迁移_提示词反推.json`

1. 上传姿态/构图参考图；
2. 预处理器优先选择 `DWPreprocessor` 或 `OpenposePreprocessor`；
3. 在功能选择开关中决定是否启用 Qwen-VL 提示词反推；
4. 修改“最大尺寸”控制输出分辨率；
5. 运行后，参考图姿态会约束 Z-Image 生成结果。

若不需要自动反推提示词，关闭相应功能可节省显存和时间。No6 依赖 No1 的 Z-Image 主模型、文本编码器、VAE 和 LoRA。

`设置国内pip源.bat` 只在国内网络安装 Python 包失败时使用：先把它复制到 `nvidia_gpu一键启动.bat` 同级目录再运行。它会把内嵌 Python 的 pip 源设为清华镜像并升级 pip；网络正常时不必运行。

### No7：SeedVR2 图片/视频超分

图片批量超分工作流：`SeedVR2_Upscaler图片批量超分.json`

1. 单图使用普通加载图片节点；
2. 批量处理时在 `LoadImageListFromDir` 的 `directory` 中填写图片文件夹绝对路径；
3. `resolution` 表示期望清晰度；
4. `max_resolution` 是防止极端长宽比撑爆显存的上限，建议先用 2048；
5. 运行后到 output 目录查看结果。

视频超分工作流：`SeedVR2_upscale视频超分.json`

该工作流原说明实测会占约 24GB 显存。当前 16GB 显卡可能非常慢或 OOM，建议先用很短的低分辨率视频测试。`torch.compile` 更适合长视频，首次会编译；启用它还可能要求安装 Microsoft Visual Studio。单图不要启用该加速节点。

### No8：Anima 可视化标签和 ControlNet

工作流：`Anima Base V1.0 ControlNet.json`

1. 通过艺术家、角色、背景、姿势等可视化选择器组合提示词；
2. 默认姿态控制节点是关闭的；
3. 需要启用时，按住 `Ctrl` 框选“姿态控制”组里的 4 个节点，再按 `Ctrl+B`；
4. 当前推荐 `DepthAnythingV2` 预处理器；
5. 启用姿态控制时，把 Anima 姿势 Tag 选择器中的姿势提示词清空，避免互相冲突。

No8 依赖 No4 的 Anima 主模型、文本编码器、VAE 和 Turbo LoRA。

### No09：SCAIL-2 视频动作迁移/人物替换

工作流：`SCAIL-2_长视频.json`

当前保留的是适合 16GB 显存的 int8 convrot 版本。

1. 上传原始动作视频；
2. 上传新人物/角色参考图；
3. 设置“最大检测目标”为视频中需要检测的人数；
4. `Window Size` 保持默认 81，不要修改；
5. “最大处理帧数”决定本次处理多长：先用 33–49 帧测试，满意后再增加；
6. 推荐先从 832×480 或 960×544 开始；
7. 如需保留原视频声音，按工作流注释连接音频节点；原视频没有声音时必须断开，否则会报错。

人物分割、RMBG、SAM、视频载入和循环拼接节点较多，首次加载或首次运行可能较慢。

### No10：LTX 2.3 音频驱动视频

推荐工作流：`LTX-2.3 音频驱动.json`，它使用当前保留的 int8 convrot 模型。

1. 上传人物/场景图片；
2. 上传 WAV、MP3 等音频；
3. 开关为 `false`：图片 + 音频驱动视频；
4. 开关为 `true`：文生视频；
5. 帧率先用 24 或 25；
6. 16GB 显存先用 832×480 或 960×544；
7. “修剪音频时长/起始位置”表示从原音频第几秒开始取段，处理长音频时可分段生成；
8. 先测试 5–10 秒，再逐渐延长。

目录中的 `(GGUF)` 工作流是备用方案，会复用 No5 的 GGUF 主模型和文本编码器；当前机器优先用无 `(GGUF)` 后缀的 int8 工作流。

### No11：局部重绘、替换万物和姿态控制

工作流：`QwenImageEdit2511局部重绘替换万物.json`

先完成 No2 模型安装，再复制 No11 的三个 LoRA。

常用模式：

- 普通图生图：所有功能开关关闭；
- 参考替换：打开“替换图片”，上传主图和参考物品/服装图；
- 局部重绘：打开“局部重绘”，在主图节点右键选择 `Open in Mask Editor / Image Canvas`，涂抹区域并保存遮罩；
- 局部替换：同时打开“局部重绘”和“替换图片”；
- 姿态控制：只打开姿态控制，上传姿势参考图，推荐 DWpose 预处理器。

提示词例子：

```text
图1人物的长发换成短发
图1人物换上图3的白色T恤和牛仔裤
图1人物手上挎着图3的包
图1人物坐在咖啡厅喝咖啡
```

姿态控制时直接描述动作，不要写“模仿图2姿势”。多个 LoRA 不要全部同时打开：

- `anime2real2511`：动漫转真人，建议 0.8–1.0；
- `Skin_Fix`：减轻皮肤油光和塑料感，建议 0.8–1.2；
- `detailz_qwen`：增强真实细节；
- `Qwen-Image-Edit-F2P`：面部一致性，常用强度约 1。

## 4. 4080 SUPER 16GB 推荐设置

| 任务 | 建议起步设置 |
|---|---|
| 文生图/图生图 | 832–1216 像素级别，批量 1 |
| Wan 2.2 视频 | 832×480 或 960×544，81 帧以内，16 FPS |
| LTX 2.3 视频 | 832×480 或 960×544，24/25 FPS，先做 5 秒 |
| SCAIL-2 | 832×480 或 960×544，先测 33–49 帧，Window Size 81 |
| SeedVR2 图片 | `max_resolution` 先设 2048 |
| SeedVR2 视频 | 16GB 显存不理想，只建议短片段试跑 |

如果发生显存不足：

1. 先降低宽和高；
2. 再减少帧数或时长；
3. 批量保持 1；
4. 关闭不需要的 Qwen-VL、ControlNet、预览或超分支路；
5. 完全退出并重启 ComfyUI，释放残留显存；
6. 不要一上来尝试 720p/1080p 长视频。

## 5. 常见问题

### 工作流出现红色方框

通常是自定义节点缺失或版本过旧。使用 Manager 的 `Install Missing Nodes`，安装后重启。仍失败时更新 ComfyUI 和对应自定义节点，但不要在正在生成时更新。

### 模型加载器显示 `null` 或找不到文件

模型放错了目录，或者文件被改名。对照工作流中的加载器和以下常见目录：

```text
models\unet
models\diffusion_models
models\text_encoders
models\vae
models\clip_vision
models\loras
models\checkpoints
models\controlnet
models\model_patches
models\latent_upscale_models
models\SEEDVR2
models\LLM
models\RMBG
models\sams
```

复制模型后如果 ComfyUI 一直开着，请重启，或刷新模型列表。

### 示例图片、视频或音频显示缺失

JSON 中保存的是作者本机示例文件名，素材包未必包含这些输入文件。这不是模型缺失：直接在 `LoadImage`、`LoadVideo`、`LoadAudio` 节点上传自己的素材即可。

### 生成的视频没有声音

检查工作流的音频线路和保存视频节点。No09 需要按注释手动连接原视频音频；无声源视频则应断开。No10 需要确认音频已正确加载，且裁剪起点没有超过音频长度。

### Manager 安装依赖失败

先确认 Git 和网络。本机 Git 已安装。国内网络确实无法访问默认 pip 源时，再使用 No6 或 No8 中的 `设置国内pip源.bat`；两个脚本内容相同，只需使用一个。

## 6. 本次清理记录

依据 16GB 显存和 64GB 内存，以下内容已从原目录移入 Windows 回收站：

- No1 中与整合包重复的“已经有 ComfyUI 只要模型和工作流”副本；
- No1 的 Git 2.54 安装包（本机已有可用 Git）；
- No2 的 `8-10G显存下载` 和 `8G以下显存下载` 两套 Q3 版本；
- No3 的 `8G显存16G内存电脑下载` 低配版本；
- No3 高配目录中未被保留工作流引用的 `umt5-xxl-encoder-Q3_K_M.gguf`；
- No09 的 `8G显存配置推荐下载` GGUF 版本。

总计约 78.64GB。它们目前仍可从回收站恢复；确认保留版本运行正常后，可手动清空 Windows 回收站以真正释放磁盘空间。不要为了释放空间删除当前保留的 `ComfyUIportable.7z`，除非已经成功解压并至少启动过一次。

## 7. 使用和安全提醒

- 部分模型文件名包含 `NSFW`、`abliterated`，也有可启用成人内容的 LoRA；不需要时保持相应节点关闭；
- 不要生成或传播违法、侵权、冒充他人、未经同意的私密内容；
- 模型和自定义节点各自可能有不同许可证，商用前应检查其来源和许可；
- `.bat`、自定义 Python 节点和第三方模型均属于外部内容，更新或加入新的第三方文件前先确认来源；
- 大模型首次加载时间长是正常现象；显存占满不等于故障，只有出现 OOM/内存不足错误时才需要降配置。
