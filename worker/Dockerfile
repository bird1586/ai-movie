# syntax=docker/dockerfile:1.7
# GPUtw 自帶映像：ComfyUI + 影音後製節點 + CosyVoice3。
# 模型一律不進映像，由 entrypoint 從 /vault/models 連結（見 models.manifest.yaml）。
# 基底用 cuda:base 而非 runtime：torch wheel 已自帶 CUDA 函式庫，省下約 1.5 GB 拉取時間。
FROM nvidia/cuda:12.8.1-base-ubuntu22.04

ARG COMFYUI_REF=v0.37.0
ARG VHS_SHA=4d907bee61e92c2e65af3bd6383a4e4d356126d1
ARG VFI_SHA=26545cc2dd95bc3d27f056016300673bdeee78f5
ARG KJNODES_SHA=d3cfe21625e5170126ce06fbfcfe1d88108688c3
ARG MMAUDIO_SHA=8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2
ARG LATENTSYNC_SHA=360d5283d7276aee68b4237b1387e594e4ce640e
ARG COSYVOICE_SHA=074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128

ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    UV_NO_CACHE=1 \
    HF_HOME=/vault/hf \
    COMFY=/opt/ComfyUI

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 映像是公開的：apt 安裝時產生的 SSH host key 要刪掉，改由 entrypoint 每次開機重新產生
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git ffmpeg openssh-server sox libsndfile1 libgl1 libglib2.0-0 tini \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssh/ssh_host_* \
    && mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv

# torch 裝在基底直譯器，ComfyUI 與 CosyVoice 兩個 venv 用 --system-site-packages 共用，只存一份。
# uv 不會把基底的套件當成已安裝，所以 venv 安裝時要用 --excludes 把 torch 排除，否則會各自再裝一份。
RUN uv python install 3.11 \
    && ln -s "$(uv python find 3.11)" /usr/local/bin/python3.11 \
    && uv pip install --python python3.11 --system --break-system-packages \
        --index-url "$TORCH_INDEX" --extra-index-url https://pypi.org/simple \
        torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
    && printf 'torch\ntorchvision\ntorchaudio\n' > /opt/torch-exclude.txt

# ComfyUI + custom nodes（全部固定 commit）
WORKDIR /opt
RUN git clone --depth 1 --branch "$COMFYUI_REF" https://github.com/comfyanonymous/ComfyUI.git "$COMFY" \
    && clone() { git -C "$COMFY/custom_nodes" clone --filter=blob:none "https://github.com/$1.git" && git -C "$COMFY/custom_nodes/${1#*/}" checkout -q "$2"; } \
    && clone Kosinkadink/ComfyUI-VideoHelperSuite "$VHS_SHA" \
    && clone Fannovel16/ComfyUI-Frame-Interpolation "$VFI_SHA" \
    && clone kijai/ComfyUI-KJNodes "$KJNODES_SHA" \
    && clone kijai/ComfyUI-MMAudio "$MMAUDIO_SHA" \
    && clone ShmuelRonen/ComfyUI-LatentSyncWrapper "$LATENTSYNC_SHA" \
    && find "$COMFY" -name .git -type d -prune -exec rm -rf {} +

WORKDIR $COMFY
RUN uv venv --python python3.11 --system-site-packages /opt/venv \
    && grep -vhE '^(torch|torchvision|torchaudio)([=<> ]|$)' \
        requirements.txt \
        custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt \
        custom_nodes/ComfyUI-Frame-Interpolation/requirements-no-cupy.txt \
        custom_nodes/ComfyUI-KJNodes/requirements.txt \
        custom_nodes/ComfyUI-MMAudio/requirements.txt \
        custom_nodes/ComfyUI-LatentSyncWrapper/requirements.txt \
        > /tmp/comfy-req.txt \
    && uv pip install --python /opt/venv/bin/python --excludes /opt/torch-exclude.txt -r /tmp/comfy-req.txt \
    && rm /tmp/comfy-req.txt

# CosyVoice3：套件版本和 ComfyUI 衝突（numpy 1.26、transformers 4.51…），放獨立 venv。
# 拿掉推論用不到的 deepspeed / tensorrt / gradio / web 伺服器，torch 用基底那份。
# openai-whisper、pyworld 等只有 sdist，建置與執行時都要 pkg_resources（setuptools 81 起移除）；
# pyworld 要 C++ 編譯器，裝完同一層就移除。
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && git clone --recurse-submodules --shallow-submodules https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice \
    && git -C /opt/CosyVoice checkout -q "$COSYVOICE_SHA" \
    && git -C /opt/CosyVoice submodule update --init --depth 1 \
    && find /opt/CosyVoice -name .git -prune -exec rm -rf {} + \
    && grep -vE '^(--|#|torch|torchaudio|deepspeed|tensorrt|gradio|fastapi|grpcio|onnxruntime-gpu)' \
        /opt/CosyVoice/requirements.txt > /tmp/cosy-req.txt \
    && printf 'onnxruntime-gpu>=1.20\nsetuptools<81\n' >> /tmp/cosy-req.txt \
    && echo 'setuptools<81' > /tmp/build-constraints.txt \
    && uv venv --python python3.11 --system-site-packages /opt/CosyVoice/.venv \
    && uv pip install --python /opt/CosyVoice/.venv/bin/python --excludes /opt/torch-exclude.txt \
        --build-constraints /tmp/build-constraints.txt -r /tmp/cosy-req.txt \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/* \
    && rm /tmp/cosy-req.txt /tmp/build-constraints.txt

# 兩個 venv 都必須用到基底那份 torch，不然映像會多出好幾 GB
RUN for py in /opt/venv/bin/python /opt/CosyVoice/.venv/bin/python; do \
        "$py" -c "import sys, torch; assert torch.__file__.startswith('/opt/python'), torch.__file__; \
assert torch.__version__.startswith('2.8.0'), torch.__version__; print(sys.executable, torch.__version__)"; \
    done

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY models.manifest.yaml /opt/models.manifest.yaml
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PATH=/opt/venv/bin:$PATH
WORKDIR /workspace
EXPOSE 22 8188
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["comfyui"]
