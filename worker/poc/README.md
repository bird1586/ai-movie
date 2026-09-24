# PoC：5 秒片頭（原始大地橫搖 + 叢林飛鳥）

第一次付費執行只做一件事：用 Wan2.2 TI2V-5B 文生影片產出一個 5 秒鏡頭，
同時量到開機、模型載入、每次生成的實際耗時與 VRAM，回填 `docs/可行性評估.md` §3、§6 的推算值。

| 項目 | 值 |
|---|---|
| 鏡頭規格 | `shots/opening_pan.json`（提示詞、兩個 seed） |
| 工作流 | `wan22_5b_t2v.json`（ComfyUI API 格式，對照官方 `video_wan2_2_5B_ti2v` 範本） |
| 輸出 | 1280×704、121 幀 @24fps = 5.04 s、h264 mp4 |
| 採樣 | uni_pc / simple、20 步、CFG 5、shift 8 |

## 需要的模型（只要這 3 個，共 18.2 GB）

放在 `/vault/models/` 下（`models.manifest.yaml` 的 `wan22_ti2v_5b`、`wan22_vae`、`umt5_xxl_fp8`）：

```
diffusion_models/wan2.2_ti2v_5B_fp16.safetensors      10.00 GB
text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors   6.74 GB
vae/wan2.2_vae.safetensors                             1.41 GB
```

## 付費執行步驟（使用者確認後才做）

> ⚠️ 下面是自帶映像的流程：2026-09-24 卡在拉映像 45 分鐘後失敗（NT$14.39）。
> **改用已驗證的一鍵流程**（官方 ComfyUI 範本 + vault 模型，細節見 `docs/PoC紀錄.md`）：
> ```sh
> python gputw_session.py shots/xianxia_clash.json shots/xianxia_flight.json   # 會先報機器與費率，問 y/N
> ```
> 自動挑機 → 開機 → 拿 Web UI cookie → 本機跑 `run_poc.py`（多個鏡頭同一次開機）→ 影片下載到 `data/poc/` → 一定 stop。
> - `--max-seeds 1`：每個鏡頭只跑 `seeds` 的第一個（選好的 seed 放第一個）。
> - `--chain`：後一鏡從前一鏡最後一格接下去，接點不跳，並自動接成 `<id1>+<id2>_s<seed>.mp4`：
>   `python gputw_session.py shots/xianxia_clash.json shots/xianxia_flight_cont.json --chain --max-seeds 1`
> - 所有工作一開始就送進佇列，GPU 不等下載。先用 `python run_poc.py … --check` 在本機看會跑幾次生成。
> 手動版：`create-instance`（ComfyUI 範本）→ `POST /instances/{id}/access-token {"port":8080}` 換 cookie →
> `COMFY_COOKIE=… RATE_NT_PER_H=<實際費率> python run_poc.py shots/a.json shots/b.json --comfy https://8080-<id>.gputw.ai --output-dir ../../data` → stop。

1. `validate-image`：`ghcr.io/bird1586/ai-movie-worker:<tag>`（免費，只確認拉得到）
2. `download-model-to-vault`：上面 3 個檔（伺服器端下載，不開 GPU；/vault 依容量計費）
3. `create-instance`：RTX 4090 單卡，`customImage.dockerImage` 用上面的 tag，
   `sshEnabled: true`、Web UI 埠 8188、`args: ["poc"]`
4. 看 logs；出現「報告：…」後，影片與 `opening_pan-*.json` 在 `/vault/outputs/poc/`
5. **立刻 stop / delete 執行個體**

`args: ["poc"]` 會啟動 ComfyUI、自動跑完兩個 seed，之後 ComfyUI 保持開著可用 Web UI 看結果。
缺模型時會在開始運算前就失敗（不浪費 GPU 時間）。

## 預估（【推算】，這次就是要量出實際值）

- 開機 + 拉 6.2 GB 映像 + 載入模型：約 3–8 分鐘
- 每次生成（5B、720p、121 幀、20 步 @4090）：約 4–9 分鐘，兩個 seed 共 8–18 分鐘
- 合計約 15–30 分鐘 ≈ **NT$4–8**（4090 NT$16.18/h）

## 不花 GPU 的本機驗證（已做過）

用 CPU 版 ComfyUI v0.37.0 + 空的佔位模型檔：

```sh
python run_poc.py shots/opening_pan.json --comfy http://127.0.0.1:8199 --dry-run
```

ComfyUI 送出時**不會**檢查 SaveVideo 的 format 與影格數步進，所以 `run_poc.py` 自己擋 4n+1 與 32 倍數，
存檔節點的設定則另外用小型合成影片實際跑過（h264、121 幀、5.04 s）。
