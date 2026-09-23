# ai-movie

以 GPU 租用為唯一付費項目的 AI 動畫製作管線。規格見 [開發計劃書.md](開發計劃書.md)、[UI.md](UI.md)，
可行性評估與修正後的成本模型見 [docs/可行性評估.md](docs/可行性評估.md)。

| 目錄 | 內容 |
|---|---|
| `backend/` | 控制平面 API（FastAPI + SQLite），uv 專案 |
| `frontend/` | 引導式 UI（Vue 3 + Naive UI） |
| `worker/` | GPU 執行端映像（ComfyUI + CosyVoice3），由 GitHub Actions 推到 GHCR；模型不進映像 |
| `deploy/systemd/` | 本機常駐服務 |
| `docs/` | 可行性評估、成本試算 |

## 本機執行

```sh
cd backend && uv sync && uv run pytest
cd frontend && npm ci && npm run build          # API 會直接提供 frontend/dist
cd backend && uv run uvicorn app.main:app --port 8110
# 開發前端：cd frontend && npm run dev            → http://127.0.0.1:5180
```

GPU 執行器目前只有 `MockExecutor`，不會呼叫任何雲端服務。
