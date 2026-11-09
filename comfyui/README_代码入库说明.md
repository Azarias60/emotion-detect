# ComfyUI 便携版（代码部分，2026-11-09 整理入 git）
#
# 目录来源：E:\Comfyui\ComfyUI\ComfyUI-Main\ComfyUIportable\ComfyUIportable\
# 本目录只含**代码与配置**；models/ 184G、input/output 运行数据按 MODELS.md 自备。

ComfyUI/
├── ComfyUI/              主程序 + custom_nodes/（30 个插件代码）
├── launch/               Windows 启动 bat + advanced/update 脚本
├── Workflows-Backup/     历史工作流 JSON（含 SCAIL-2 长视频 GGUF 版）
├── MODELS.md             183G 模型清单（迁移/重装按此自备）
├── README.md             原 ComfyUI 素材包说明
└── 本期说明.txt

## Windows 本机恢复用法

代码在原位 `E:\Comfyui\ComfyUI\ComfyUI-Main\ComfyUIportable\ComfyUIportable\`
（本 git 副本仅作备份/版本管理）。models/ 若丢失按 MODELS.md 清单拷贝回：
`ComfyUI-Main\ComfyUIportable\ComfyUIportable\ComfyUI\models\`

## Linux 部署

不要用这个目录——用 `deploy/comfyui-int8/`（自包含部署包 + e2e_test.sh）。
