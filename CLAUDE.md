# CLAUDE.md

AI 動畫製作管線。唯一付費項目是 GPUtw 的 GPU 租用，其餘全部免費或本機。
規格：`開發計劃書.md`、`UI.md`；修正後的成本模型與問題清單：`docs/可行性評估.md`；
PoC 實測紀錄與優化清單：`docs/PoC紀錄.md`（**動 GPU 之前先讀這份**）。

## 目錄與本機服務

| 路徑 | 內容 |
|---|---|
| `backend/` | FastAPI + SQLite 控制平面（uv 專案，Python 3.12）。`uv run pytest` |
| `frontend/` | Vue 3 + Naive UI。`npm run build` 後由 API 直接提供 `frontend/dist` |
| `worker/` | GPU 映像：`Dockerfile`、`entrypoint.sh`、`models.manifest.yaml`、`poc/` |
| `deploy/systemd/` | `ai-movie-api.service`（user unit，已安裝啟用） |
| `data/` | 本機資料與下載回來的影片（gitignored），PoC 輸出在 `data/poc/` |

- 主機：Oracle A1 **aarch64**、無 Docker、磁碟有限。bpy 沒有 aarch64 wheel。
- 埠：API **8110**（systemd `ai-movie-api`）、Vite dev **5180**。5173、8100、9000 已被別的服務占用。
- 重啟 API：`systemctl --user restart ai-movie-api`
- GPU 執行器只有 `MockExecutor`，GPUtw 執行器尚未實作。

## GPU 映像（worker）

- GitHub Actions `.github/workflows/build-worker.yml`：push 到 main 且改到 `worker/**` 時自動建置，
  推到**公開** GHCR `ghcr.io/bird1586/ai-movie-worker:<commit SHA 前 12 碼>`（公開 repo + 公開套件＝免費）。
  GPUtw 不接受 `latest` tag。
- **workflow 檔不能從這台機器 push**：gh/git token 沒有 `workflow` scope。要改 `.github/workflows/*`
  就把內容印給使用者，請使用者在 GitHub 網頁編輯器貼上。貼上時縮排會跑掉，所以檔案用 JSON 風格的 YAML。
- 映像內容：CUDA 12.8 base、Python 3.11、torch 2.8.0 cu128 裝在基底直譯器，ComfyUI（`/opt/venv`）與
  CosyVoice（`/opt/CosyVoice/.venv`）兩個 venv 用 `--system-site-packages` + `--excludes` 共用，
  建置時會用 assert 檢查。不這樣做 torch 會被裝三份，映像會從 6.2 GB 膨脹到 11.9 GB。
- 映像是公開的，**不能放任何祕密**：SSH host key 在建置時刪除，由 entrypoint 開機時重新產生。
- 模型一律不進映像，放 GPUtw `/vault/models/<類別>/`，由 entrypoint 連結到 ComfyUI。
- entrypoint 模式（當成 `args` 傳入）：`comfyui`（預設）、`poc [shot.json]`、`idle`、其他指令直接 exec。
  環境變數 `OUTPUT_DIR` 控制輸出位置（預設 `/vault/outputs`）。

## GPUtw 使用原則（使用者最在意省錢）

- 使用者明確同意之前，**不要開任何執行個體**。開之前先把數字報給使用者：GPU、單價、預估時間、預估費用。
- 帳戶餘額不多（2026-09 約 NT$100）。4090 常常售完，5090 32GB 約 $0.60/h 起。
- 計費從**拉映像**就開始算；閒置照樣計費。跑完**立刻 stop**（stop 會保留 /workspace，delete 前要先確認）。
- **GPU 節點拉自帶映像非常慢**：第 1 次 PoC 拉 6.2 GB 映像，45 分鐘沒拉完就被平台標 FAILED，
  白花 NT$14。**生影片一律用官方 `gputw/comfyui` 範本 + vault 模型**（第 2 次 PoC 成功，NT$6.47）。
  自帶映像留給官方範本做不到的事（CosyVoice、LatentSync），而且要縮到 1 GB 以下。
  部署時要設停損：拉映像超過 15 分鐘就 stop。
- **遠端操作流程**（已驗證）：
  - API key 在 `~/.config/gputw/key`（600）。使用方式：`curl -H "Authorization: Bearer $(< ~/.config/gputw/key)"`。
    **絕對不要印出、echo 或 head 這個檔案**。Claude 外掛自己儲存的 key 不能讀（auto mode 會擋）。
  - REST base 是 `https://api.gputw.ai/api`。`POST /instances/{id}/access-token {"port":8080}` 拿到 url，
    用 `curl -c cookiejar -L "$url"` 換成 cookie，再把 cookie 放進 `COMFY_COOKIE` 給 `run_poc.py --comfy https://8080-<id>.gputw.ai`。
  - 停機：`POST /instances/stop {"instanceId": …}`，或用 MCP `stop-instance`。
- 實測（5090、720p、121 幀、20 步）：熱機 150 s／鏡頭，冷啟動多 90 s，VRAM 峰值 24 GB。
  跑 `run_poc.py` 時用 `RATE_NT_PER_H` 設定實際費率。
- 單價的 `hourlyRate` 是**美元**（0.5994 ≈ NT$18.9/h），帳戶餘額是新台幣（`get-vault-stats` 的 `balanceNtd`）。
- `/vault` 每月 NT$2/GB。模型用 `download-model-to-vault`（`hf:owner/repo:path` 或 URL）在伺服器端下載，
  不用開 GPU。
- 影片不要放在 `/vault`：`run_poc.py` 會在執行個體還開著時，透過 ComfyUI 的 `/view` 把影片下載到本機 `data/poc/`。
- PoC 步驟與實測時間、費用：`worker/poc/README.md`、`docs/PoC紀錄.md`。

## 公開 repo 規則

- repo `bird1586/ai-movie` 是**公開**的：不放金鑰、token、`.env`；`data/`、`.claude/`、`.serena/` 都已 gitignore。
- 不可出現既有電影、遊戲的 IP 名稱（角色、武器、片名）。風格參考要寫成原創描述。
- commit 訊息結尾加上 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；不要改使用者的全域 git 設定。
- 文件用繁體中文。
