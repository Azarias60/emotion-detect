# vendor/classifier

第三方表情分类推理子集（原 `face_detection/classifier/` 的活链部分：`inference.py`、
`models/build_models.py`、`transforms/build_transform.py` 及各 `__init__.py`）。

来源：PanJinquan（390737991@qq.com）随原仓库携带的训练库代码切片，无公开上游 pip 包
（`basetrainer` 1.0.0 / `pybaseutils` 2.4.34 的同名实现与本地 checkpoint 加载行为不一致，
故外挂隔离到 vendor/）。

请勿手工改动本目录代码；预训练权重仍在原位置
`face_detection/classifier/models/pretrained/`（未随包移动），推理路径
`pretrained=False` 不依赖它们。
