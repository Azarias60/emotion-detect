# -*- coding: utf-8 -*-
"""LTX-2.3 首尾帧过渡图构建（v22 提示词 + v28 接线配方）。

配方要点（全部经过实片验证，改动前先看 README「已知坑」）：
- 双端 LTXVAddGuide strength=0.85（节点 27 首帧 frame_idx=0、节点 28 尾帧 frame_idx=-1）
- 不加中间锚点（旧版节点 29 已删除：加了会让头部前 8 帧冻住）
- 帧数必须 1+8n（默认 81 帧 = 4.05s@20fps，加长可缓解尾部收敛抖动）
- CFGGuider cfg=1 + ManualSigmas 8 步（蒸馏模型原生配置）
- 分辨率 704x1280，输出 20fps，NVENC h264，无音频
"""
SIGMAS = "1., 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"

L = lambda nid, slot=0: [str(nid), slot]


def build(first_img, last_img, prefix, *, pos, neg, nag, frames=81,
          seed=380733347922385, guide=0.85, fps=20, width=704, height=1280,
          stg_scale=1.1, stg_blocks="29", cfg=2.5, ltx25=False):
    """first_img/last_img：ComfyUI input 里的图片文件名；prefix：输出文件名前缀。

    v34 起新增：LTXVSpatioTemporalGuidance（stg_scale 默认 1.1，官方 STG 参数，
    直接管帧间稳定性——外部实测指南推荐 1.0~1.4）+ cfg 1.0->2.5（cfg=1 时提示词
    几乎不起作用，提升后"大小远近不变"类锁定语才真正咬合；指南建议 2.5~3.5 不超4）。

    ltx25=True 换 LTX 2.5 int8 全套（租卡机 24G 用，2026-11-09）：
      主模型 GGUF Q4 → 原生 UNETLoader 加载 ltx-2.5-22b-distilled-transformer-
        comfy-int8-convrot.safetensors（20GB，int8 convrot 效果对齐 SCAIL int8）
      双 CLIPLoaderGGUF → 单 CLIPLoader 加载 gemma4-12b-with-proj-ltx-2.5-
        comfy-int8-convrot.safetensors（14.3GB，含 text projection）
      VAE LTX23_* → ltx-2.5-{video-vae-conv,audio-vae}-bf16
    其余采样链（LTXVAddGuide/NAG/STG/CFG）不变——LTXV 节点为 ComfyUI 原生，
    2.3/2.5 通用。**注意：2.5 图未在本机验证过（34GB 权重跑不动 16G 卡），
    租卡机先冒烟一个 9 帧过渡再批量。**
    """
    p = {
        "1": {"class_type": "DualCLIPLoaderGGUF", "inputs": {
            "clip_name1": "gemma-3-12b-it-IQ4_XS.gguf",
            "clip_name2": "ltx-2.3_text_projection_bf16.safetensors", "type": "ltxv"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": L("1")}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": L("1")}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": nag, "clip": L("1")}},
        "10": {"class_type": "GGUFLoaderKJ", "inputs": {
            "model_name": "ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf", "extra_model_name": "none",
            "dequant_dtype": "default", "patch_dtype": "default", "patch_on_device": False,
            "enable_fp16_accumulation": False, "attention_override": "none"}},
        "14": {"class_type": "LTXVSpatioTemporalGuidance", "inputs": {
            "model": L("10"), "scale": stg_scale, "blocks": stg_blocks,
            "start_percent": 0.0, "end_percent": 1.0}},
        "11": {"class_type": "LTX2_NAG", "inputs": {
            "model": L("14"), "nag_cond_video": L("4"), "nag_scale": 20,
            "nag_alpha": 0.25, "nag_tau": 2.5, "inplace": False}},
        "12": {"class_type": "VAELoaderKJ", "inputs": {
            "vae_name": "LTX23_video_vae_bf16.safetensors", "device": "main_device",
            "weight_dtype": "bf16"}},
        "13": {"class_type": "LTXVAudioVAELoader", "inputs": {
            "ckpt_name": "LTX23_audio_vae_bf16.safetensors"}},
        "30": {"class_type": "LoadImage", "inputs": {"image": first_img}},
        "31": {"class_type": "LoadImage", "inputs": {"image": last_img}},
        "20": {"class_type": "ResizeImageMaskNode", "inputs": {
            "input": L("30"), "resize_type": "scale dimensions",
            "resize_type.width": width, "resize_type.height": height,
            "resize_type.crop": "center", "scale_method": "nearest-exact"}},
        "21": {"class_type": "LTXVPreprocess", "inputs": {"image": L("20"), "img_compression": 25}},
        "22": {"class_type": "ResizeImageMaskNode", "inputs": {
            "input": L("31"), "resize_type": "scale dimensions",
            "resize_type.width": width, "resize_type.height": height,
            "resize_type.crop": "center", "scale_method": "nearest-exact"}},
        "23": {"class_type": "LTXVPreprocess", "inputs": {"image": L("22"), "img_compression": 25}},
        "24": {"class_type": "GetImageSize", "inputs": {"image": L("20")}},
        "25": {"class_type": "LTXVConditioning", "inputs": {
            "positive": L("2"), "negative": L("3"), "frame_rate": fps}},
        "26": {"class_type": "EmptyLTXVLatentVideo", "inputs": {
            "width": L("24", 0), "height": L("24", 1), "length": frames, "batch_size": 1}},
        "27": {"class_type": "LTXVAddGuide", "inputs": {
            "positive": L("25", 0), "negative": L("25", 1), "vae": L("12"),
            "latent": L("26"), "image": L("21"), "frame_idx": 0, "strength": guide}},
        "28": {"class_type": "LTXVAddGuide", "inputs": {
            "positive": L("27", 0), "negative": L("27", 1), "vae": L("12"),
            "latent": L("27", 2), "image": L("23"), "frame_idx": -1, "strength": guide}},
        "32": {"class_type": "LTXVEmptyLatentAudio", "inputs": {
            "audio_vae": L("13"), "frames_number": frames, "frame_rate": fps, "batch_size": 1}},
        "40": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS}},
        "41": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "42": {"class_type": "SamplerEulerAncestral", "inputs": {"eta": 0, "s_noise": 1}},
        "43": {"class_type": "CFGGuider", "inputs": {
            "model": L("11"), "positive": L("28", 0), "negative": L("28", 1), "cfg": cfg}},
        "44": {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": L("28", 2), "audio_latent": L("32")}},
        "45": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": L("41"), "guider": L("43"), "sampler": L("42"),
            "sigmas": L("40"), "latent_image": L("44")}},
        "46": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": L("45", 1)}},
        "47": {"class_type": "LTXVCropGuides", "inputs": {
            "positive": L("28", 0), "negative": L("28", 1), "latent": L("46", 0)}},
        "48": {"class_type": "VAEDecodeTiled", "inputs": {
            "samples": L("47", 2), "vae": L("12"), "tile_size": 384, "overlap": 64,
            "temporal_size": 4096, "temporal_overlap": 64}},
        "50": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": L("48"), "frame_rate": fps, "loop_count": 0,
            "filename_prefix": prefix, "format": "video/nvenc_h264-mp4",
            "pingpong": False, "save_output": True, "pix_fmt": "yuv420p",
            "bitrate": 10, "megabit": True, "save_metadata": True}},
    }
    if ltx25:
        # LTX 2.5 int8 全套（详见 docstring）：换 4 个 loader，采样链不动
        p["1"] = {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            "type": "ltxv"}}
        p["10"] = {"class_type": "UNETLoader", "inputs": {
            "unet_name": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            "weight_dtype": "default"}}
        p["12"] = {"class_type": "VAELoaderKJ", "inputs": {
            "vae_name": "ltx-2.5-video-vae-conv-bf16.safetensors",
            "device": "main_device", "weight_dtype": "bf16"}}
        p["13"] = {"class_type": "LTXVAudioVAELoader", "inputs": {
            "ckpt_name": "ltx-2.5-audio-vae-bf16.safetensors"}}
    return p
