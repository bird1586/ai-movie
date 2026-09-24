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
- **標準做法：一個指令跑完一批鏡頭**（第 3 次 PoC 驗證過，全程無人介入）：
  ```sh
  cd worker/poc && python gputw_session.py shots/a.json shots/b.json --yes   # 使用者同意花費後才加 --yes
  ```
  挑最便宜的空機（4090 → 5090，≤ US$0.8/h）→ 官方範本 → 等 RUNNING（開機停損 900 s）→ 拿 cookie →
  本機跑 `run_poc.py`（影片下載到 `data/poc/`）→ `finally` 一定 stop，扣款與秒數寫進 `data/poc/session-*.json`。
  用 `run_in_background` 跑，不要自己 sleep 輪詢。**所有鏡頭排進同一次開機**，不要一個鏡頭開一次機。
  - API key 在 `~/.config/gputw/key`（600），腳本自己讀。手動用只能這樣：`curl -H "Authorization: Bearer $(< ~/.config/gputw/key)"`。
    **絕對不要印出、echo 或 head 這個檔案**。Claude 外掛自己儲存的 key 不能讀（auto mode 會擋）。
  - 手動步驟（腳本壞掉時）：REST base `https://api.gputw.ai/api`；`POST /instances/{id}/access-token {"port":8080}`
    拿到的 url 90 秒內用 cookie jar 開一次換成 cookie → `COMFY_COOKIE` → `run_poc.py --comfy https://8080-<id>.gputw.ai`；
    停機 `POST /instances/stop {"instanceId": …}`。
- 實測（5090、720p、121 幀、20 步）：官方範本開機 **27 s**；熱機 **150–170 s／5 秒鏡頭**；
  第一個鏡頭要從 vault 載入模型，**依節點差很多**（`72ca950b` +90 s、`b4d0f38c` +420 s）；VRAM 峰值 25 GB。
  一次開機跑 N 個 5 秒鏡頭 ≈ 冷載入 + N × 170 s。
- **提示詞（Wan2.2 5B）**：鏡頭運動要寫在**提示詞最前面**（"The camera pans…"、"Tracking shot…"、"Slow motion, the camera orbits…"），
  寫在中間幾乎沒作用。負面提示詞**不要**放「镜头抖动」等鏡頭相關詞，會把整個運鏡壓掉（第 2 次 PoC 就是這樣變成靜態畫面）。
  新鏡頭第一次試拍給 2 個 seed，看聯絡表（contact sheet）挑一個，**把選中的 seed 移到 `seeds` 第一個**；
  之後重跑一律加 `--max-seeds 1`（費用減半）。
- **多鏡頭接成一段**用 `--chain`：整串變成一個 ComfyUI 工作流，後一鏡用前一鏡最後一格當 `start_image`
  （`ImageFromBatch` → `Wan22ImageToVideoLatent.start_image`），本機再用 ffmpeg 去掉重複的那一格接成一支影片。
  後一鏡的提示詞要**從前一鏡最後的畫面寫起**（例：`shots/xianxia_flight_cont.json`）。
- `run_poc.py` 一開始就把全部工作送進 ComfyUI 佇列，GPU 算下一支時本機同時下載上一支。
  `--check` 只在本機組工作流、不連線，改完鏡頭規格先跑這個。
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
