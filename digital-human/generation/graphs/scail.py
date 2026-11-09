# -*- coding: utf-8 -*-
"""SCAIL-2 动作迁移图构建：以用户 UI 验证过的参数快照为底，只改输入。

快照来源：recipes/scail_{greet,idle}_base.json（ComfyUI history 导出，prompt 在 index 2）。
快照即权威——模型/采样器/窗口循环/ColorTransfer 拓扑全部保持用户原样，
本模块只覆盖以下输入点（v28 实证清单）：

  3    CLIPTextEncode  正向提示词（长详细描述：人物外观+动作+场景+稳定性）
  4    CLIPTextEncode  负向提示词（节点 4 实际接进 WanSCAILToVideo 114/182，
                                      喂文本即生效——2026-11-08 起由配方
                                      negative_prompt 提供，不再置空）
  30   LoadImage       参考图（默认保持快照里的 5186 图）
  33   VHS_LoadVideo   姿态视频文件名
  33   VHS_LoadVideo   force_rate=20（源 30fps → 输出 20fps，时长=原视频时长）
  220  INTConstant     frame_load_cap = 姿态视频实际帧数（不裁不补）
  177  CreateVideo     fps = 20
  176  SaveVideo       filename_prefix

replacement_mode 保持 false（快照原值）：人物和背景都跟随参考图，
动作跟随姿态视频；开启替换会在画面里出现两张脸。
"""
import json
import os

import config

BASES = {"greet": "scail_greet_base.json", "idle": "scail_idle_base.json"}

# 构图里允许覆盖的节点白名单（防手滑改坏用户拓扑）
_OVERRIDABLE = {"3", "4", "30", "33", "220", "177", "176"}


def load_base(which):
    path = os.path.join(config.RECIPES_DIR, BASES[which])
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(which, *, pose_video, cap, prefix, prompt, negative="",
          force_rate=20, fps=20, ref_image=None):
    """which: 'greet' | 'idle'（选择底快照）。pose_video: input 内的文件名。"""
    p = json.loads(json.dumps(load_base(which)))
    p["3"]["inputs"]["text"] = prompt
    p["4"]["inputs"]["text"] = negative
    if ref_image:
        p["30"]["inputs"]["image"] = ref_image
    p["33"]["inputs"]["video"] = pose_video
    p["33"]["inputs"]["force_rate"] = force_rate
    p["220"]["inputs"]["value"] = cap
    p["177"]["inputs"]["fps"] = fps
    p["176"]["inputs"]["filename_prefix"] = prefix
    return p


def output_path(prefix):
    """SaveVideo 命名规则：<prefix>_00001_.mp4（前缀可含子目录）。"""
    return os.path.join(config.COMFY_OUTPUT, prefix + "_00001_.mp4")
