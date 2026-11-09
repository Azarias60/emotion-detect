# LiveTalking Docker Image
# 基于 CUDA 11.6 + Ubuntu 20.04

ARG BASE_IMAGE=nvcr.io/nvidia/cuda:11.6.1-cudnn8-devel-ubuntu20.04
FROM $BASE_IMAGE

RUN apt-get update -yq --fix-missing \
 && DEBIAN_FRONTEND=noninteractive apt-get install -yq --no-install-recommends \
    pkg-config \
    wget \
    cmake \
    curl \
    git \
    vim \
    ffmpeg

# 安装 Miniconda
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
 && sh Miniconda3-latest-Linux-x86_64.sh -b -u -p ~/miniconda3 \
 && ~/miniconda3/bin/conda init

# 创建 Python 环境
RUN conda create -n livetalking python=3.10 -y
SHELL ["conda", "run", "-n", "livetalking", "/bin/bash", "-c"]

# 安装 PyTorch
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
 && conda install pytorch==1.12.1 torchvision==0.13.1 cudatoolkit=11.3 -c pytorch -y

# 安装项目依赖
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 复制项目代码
COPY . /LiveTalking
WORKDIR /LiveTalking

# 启动服务（WebRTC 模式，默认端口 8010）
# 运行前请先下载模型到 models/ 目录
EXPOSE 8010
EXPOSE 50000
CMD ["python", "app.py", "--transport", "webrtc", "--model", "wav2lip", "--avatar_id", "my_avatar"]
