# TongueDiagnosis — 中医舌诊推理包

基于 EfficientNet-B0 的舌象四维特征检测与中医体质判定推理子集。从舌象图片中自动检测舌色、苔色、裂纹、齿痕四个维度，依据 GB/T 46939—2025《中医体质分类与判定》国家标准映射为 9 种中医体质类型。

本包是 `Tongue_diagnosis` 完整项目的推理子集（已剥离 GUI、HTTP 服务与训练代码），作为后端服务的内嵌模型包使用，由 `app_api.py` 在启动时装载。

---

## 包结构

```text
tongue_diagnosis/                    # Python 包根
├── README.md
├── __init__.py
├── constitution_model.py           # ★ 体质映射引擎（纯数据，零依赖）
│
└── efficient_net/                  # 模型包
    ├── __init__.py
    ├── predictor.py                # ★ 统一推理接口 TonguePredictor
    │
    ├── efficientnet/
    │   └── model.py                 # EfficientNet-B0 网络结构（MBConv + SE）
        │
        ├── tongue_crack/           # 裂纹检测（2 分类）
        │   ├── class_indices.json  # {"0":"crack", "1":"normal"}
        │   └── weights/model-29.pth
        │
        ├── tongue_coated/          # 苔色检测（3 分类）
        │   ├── class_indices.json  # {"0":"nocoated","1":"white","2":"yellow"}
        │   └── weights/model-29.pth
        │
        ├── tongue_color/           # 舌色检测（3 分类）
        │   ├── class_indices.json  # {"0":"crimson","1":"red","2":"white"}
        │   └── weights/model-29.pth
        │
        └── tongue_indentation/     # 齿痕检测（2 分类）
            ├── class_indices.json  # {"0":"indentation","1":"normal"}
            └── weights/model-29.pth
```

---

## 系统流程

```text
舌象图片 → TonguePredictor.predict() → 四维舌象标签
        → infer_constitution_from_tongue()（36 条国标规则匹配）
        → get_body_mind_report()（身心健康评估报告）
```

**数据维度**：图片 → Resize/CenterCrop 224×224 → EfficientNet-B0 → softmax → argmax → 标签 → 规则匹配 → 体质报告。

---

## 使用方式

后端统一通过 `TonguePredictor` 推理（路径基于 `__file__`，不依赖工作目录）：

```python
from tongue_diagnosis.efficient_net.predictor import TonguePredictor

predictor = TonguePredictor(device=None)   # None=自动选 CUDA，回退 CPU
result = predictor.predict("tongue.jpg")   # dict，四维分类结果与置信度
json_str = predictor.predict_json("tongue.jpg")  # JSON 字符串
```

体质映射由 `constitution_model.py` 提供：

```python
from tongue_diagnosis.constitution_model import (
    infer_constitution_from_tongue,
    get_body_mind_report,
)
```

---

## 模型规格

| 项目 | 值 |
|------|-----|
| 网络结构 | EfficientNet-B0 × 4（共享同一结构，独立权重） |
| 输入尺寸 | 224 × 224 × 3 (RGB) |
| 预处理 | Resize(224) → CenterCrop(224) → ToTensor |
| 推理设备 | CPU（默认）/ CUDA |
| 首次加载耗时 | ~2.5s (CPU) |
| 单次推理耗时 | ~5s (CPU，4 模型) / ~0.07s (GPU) |
| 权重体积 | 4 × 16MB = 64MB |

### 输出标签

| 维度 | 类别数 | 标签映射 |
|------|--------|----------|
| crack（裂纹） | 2 | `crack`=裂纹舌, `normal`=无裂纹 |
| coated（苔色） | 3 | `white`=白苔, `yellow`=黄苔, `nocoated`=无苔 |
| color（舌色） | 3 | `red`=淡红舌, `white`=淡白舌, `crimson`=深红舌 |
| indentation（齿痕） | 2 | `indentation`=齿痕舌, `normal`=无齿痕 |

---

## 体质映射说明

- **依据**：GB/T 46939—2025《中医体质分类与判定》国家标准。
- **判定方式**：规则匹配（非 ML 分类）。3(舌色) × 3(苔色) × 2(裂纹) × 2(齿痕) = 36 种组合全覆盖；每条规则含 1~3 个候选体质，权重 3=核心命中、2=强关联、1=可能关联。
- **9 种体质**：平和质（正常体质）与气虚质、阳虚质、阴虚质、痰湿质、湿热质、血瘀质、气郁质、特禀质（偏颇体质）。

### 已知局限

- 模型无法检测苔质（薄/厚/腻）和舌体大小（胖大/瘦小），痰湿质只能间接推断
- 模型不包含"非舌象"类别，对任意图片都会输出分类结果
- 血瘀检测缺少舌下静脉成像

---

## 异常处理

`TonguePredictor` 提供 5 级异常分类：

| 异常 | 触发条件 |
|------|---------|
| `ModelLoadError` | 权重文件缺失/损坏/结构不匹配 |
| `ImageNotFoundError` | 图片路径不存在 |
| `InvalidImageError` | 文件无法解码为图片 |
| `InferenceError` | 模型前向传播异常 |
| `TonguePredictorError` | 以上所有异常的基类 |
