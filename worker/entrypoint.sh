#!/usr/bin/env bash
# 開機：sshd → 把 /vault 模型連結進 ComfyUI → 啟動服務。
# GPUtw 的 sshEnabled 會注入 root 的 authorized_keys，這裡只負責讓 sshd 在 22 埠跑起來。
set -euo pipefail

VAULT_MODELS=${VAULT_MODELS:-/vault/models}
OUTPUT_DIR=${OUTPUT_DIR:-/vault/outputs}

ssh-keygen -A  # 只補缺少的 host key；映像內不含任何私鑰
/usr/sbin/sshd

link() {  # link <來源> <目標>：目標已存在就換成連結
    mkdir -p "$1"
    rm -rf "$2"
    ln -s "$1" "$2"
}

if [ -d /vault ]; then
    # ComfyUI 標準模型目錄：/vault/models/<類別> → $COMFY/models/<類別>
    for dir in diffusion_models text_encoders vae clip_vision loras upscale_models audio_encoders mmaudio; do
        link "$VAULT_MODELS/$dir" "$COMFY/models/$dir"
    done
    # 自己管 checkpoint 路徑的節點
    link "$VAULT_MODELS/latentsync" "$COMFY/custom_nodes/ComfyUI-LatentSyncWrapper/checkpoints"
    link "$VAULT_MODELS/vfi" "$COMFY/custom_nodes/ComfyUI-Frame-Interpolation/ckpts"
    link "$VAULT_MODELS/cosyvoice" /opt/CosyVoice/pretrained_models
else
    echo "[entrypoint] 沒有 /vault，模型目錄維持空的，輸出寫到 /workspace" >&2
    OUTPUT_DIR=/workspace/outputs
fi
mkdir -p "$OUTPUT_DIR"

case "${1:-comfyui}" in
    comfyui)
        shift || true
        exec python "$COMFY/main.py" --listen 0.0.0.0 --port 8188 --output-directory "${OUTPUT_DIR}" "$@"
        ;;
    poc)  # 開 ComfyUI 並自動跑一個鏡頭規格，跑完 ComfyUI 繼續開著（可從 Web UI 看結果）
        shot=${2:-/opt/poc/shots/opening_pan.json}
        python "$COMFY/main.py" --listen 0.0.0.0 --port 8188 --output-directory "${OUTPUT_DIR}" &
        comfy_pid=$!
        python /opt/poc/run_poc.py "$shot" --output-dir "$OUTPUT_DIR" 2>&1 | tee "$OUTPUT_DIR/poc-last.log" || true
        wait "$comfy_pid"
        ;;
    idle)  # 只開 SSH，例如下載模型或除錯時
        exec sleep infinity
        ;;
    *)
        exec "$@"
        ;;
esac
