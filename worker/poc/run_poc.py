"""GPUtw PoC：把一個鏡頭規格丟給容器內的 ComfyUI，量測每次生成的耗時、VRAM 與成本。

只用標準函式庫，容器內直接跑：
    python /opt/poc/run_poc.py /opt/poc/shots/opening_pan.json
不花 GPU 也能先檢查工作流（ComfyUI 會做完整驗證，接著立刻取消）：
    python run_poc.py shots/opening_pan.json --dry-run
可以一次給多個鏡頭，同一次開機依序跑完（只載入一次模型）。
也可以在本機跑，對遠端 ComfyUI（例如 GPUtw 官方範本的 Web UI）送工作，影片用 /view 下載回來：
    COMFY_COOKIE='…' python run_poc.py shots/opening_pan.json --comfy https://8080-<id>.gputw.ai --output-dir ../../data

每個 seed 生成一次；第一次含模型載入（冷啟動），之後是熱機時間，兩者分開記錄。
結果寫到 <輸出目錄>/poc/<shot id>-<時間>.json，影片在同一個目錄。
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RATE_NT_PER_H = float(os.environ.get("RATE_NT_PER_H", 16.18))  # 預設 RTX 4090，與 backend/app/budget.py 相同
SAVE_NODE = "58"
# 遠端 ComfyUI 在 GPUtw 的私有 HTTP 埠後面，要帶 access-token 換來的 cookie
HEADERS = {"User-Agent": "ai-movie-poc/1.0"} | ({"Cookie": os.environ["COMFY_COOKIE"]} if os.environ.get("COMFY_COOKIE") else {})


def call(base: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **HEADERS},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path} → HTTP {e.code}: {e.read().decode(errors='replace')}") from None
    return json.loads(raw) if raw else {}


def wait_for_comfy(base: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return call(base, "/system_stats")
        except (urllib.error.URLError, ConnectionError, RuntimeError):
            if time.monotonic() > deadline:
                raise SystemExit(f"ComfyUI 在 {timeout_s:.0f}s 內沒有回應：{base}")
            time.sleep(2)


def container_uptime_s() -> float | None:
    """容器（PID 1）啟動到現在的秒數，用來估算開機 + 拉映像後到可用的時間。"""
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        start_ticks = int(Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()[19])
        return round(uptime - start_ticks / os.sysconf("SC_CLK_TCK"), 1)
    except (OSError, ValueError, IndexError):
        return None


def build_prompt(shot: dict, seed: int) -> dict:
    # ComfyUI 送出時不檢查這兩項，錯了會在 GPU 算完才失敗或被默默截斷
    if shot["frames"] % 4 != 1:
        raise SystemExit(f"frames 必須是 4n+1，收到 {shot['frames']}（見 docs/可行性評估.md §4）")
    if shot["width"] % 32 or shot["height"] % 32:
        raise SystemExit(f"寬高必須是 32 的倍數，收到 {shot['width']}×{shot['height']}")
    wf = json.loads((HERE / shot["workflow"]).read_text())
    wf["6"]["inputs"]["text"] = shot["prompt"]
    wf["7"]["inputs"]["text"] = shot["negative"]
    wf["55"]["inputs"].update(width=shot["width"], height=shot["height"], length=shot["frames"])
    wf["3"]["inputs"].update(seed=seed, steps=shot["steps"], cfg=shot["cfg"])
    wf["57"]["inputs"]["fps"] = shot["fps"]
    wf[SAVE_NODE]["inputs"]["filename_prefix"] = f"poc/{shot['id']}_s{seed}"
    return wf


def check_models(base: str, wf: dict) -> list[str]:
    """開始算之前先確認模型檔都在，避免 GPU 空轉到一半才失敗。"""
    missing = []
    for node in wf.values():
        cls = node["class_type"]
        for key, value in node["inputs"].items():
            if not (isinstance(value, str) and value.endswith((".safetensors", ".pt", ".pth"))):
                continue
            spec = call(base, f"/object_info/{cls}")[cls]["input"]["required"][key]
            options = spec[0] if isinstance(spec[0], list) else spec[1].get("options", [])
            if value not in options:
                missing.append(f"{cls}.{key} = {value}")
    return missing


class VramSampler(threading.Thread):
    """每秒讀一次 /system_stats，記錄 VRAM 使用峰值。"""

    def __init__(self, base: str):
        super().__init__(daemon=True)
        self.base, self.peak_gb, self.stop = base, 0.0, threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                dev = call(self.base, "/system_stats")["devices"][0]
                self.peak_gb = max(self.peak_gb, (dev["vram_total"] - dev["vram_free"]) / 1e9)
            except Exception:
                pass
            self.stop.wait(1)


def run_once(base: str, wf: dict, client_id: str) -> dict:
    sampler = VramSampler(base)
    sampler.start()
    t0 = time.monotonic()
    pid = call(base, "/prompt", {"prompt": wf, "client_id": client_id})["prompt_id"]
    while True:
        hist = call(base, f"/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed") is not None:
            break
        time.sleep(2)
    elapsed = time.monotonic() - t0
    sampler.stop.set()

    status = hist["status"]
    if status.get("status_str") != "success":
        errors = [m for m in status.get("messages", []) if m[0] == "execution_error"]
        raise RuntimeError(f"生成失敗：{json.dumps(errors, ensure_ascii=False)[:2000]}")
    out = hist["outputs"][SAVE_NODE]["images"][0]
    return {
        "prompt_id": pid,
        "seconds": round(elapsed, 1),
        "vram_peak_gb": round(sampler.peak_gb, 2),
        "file": str(Path(out.get("subfolder", "")) / out["filename"]),
        "output": out,
    }


def fetch_output(base: str, out: dict, dest: Path) -> None:
    """本機沒有這個檔案（ComfyUI 在遠端）就從 /view 下載。"""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({"filename": out["filename"], "subfolder": out.get("subfolder", ""), "type": "output"})
    req = urllib.request.Request(f"{base}/view?{query}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def probe(path: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(r.stdout)
    except (OSError, subprocess.CalledProcessError) as e:
        return {"error": str(e)}


def run_shot(args, shot: dict, stats: dict, client_id: str, cold_first: bool, uptime_at_start) -> None:
    dev = stats["devices"][0]
    runs = []
    for i, seed in enumerate(shot["seeds"]):
        cold = cold_first and i == 0
        print(f"▶ {shot['id']} seed {seed}：{'冷啟動（含載入模型）' if cold else '熱機'} …", flush=True)
        r = run_once(args.comfy, build_prompt(shot, seed), client_id)
        fetch_output(args.comfy, r.pop("output"), args.output_dir / r["file"])
        r.update(seed=seed, cold=cold, video=probe(args.output_dir / r["file"]))
        r["cost_nt"] = round(r["seconds"] / 3600 * RATE_NT_PER_H, 2)
        runs.append(r)
        print(f"  {r['seconds']} s · VRAM 峰值 {r['vram_peak_gb']} GB · NT${r['cost_nt']} · {r['file']}", flush=True)

    out_s = shot["frames"] / shot["fps"]
    warm = [r["seconds"] for r in runs if not r["cold"]] or [runs[0]["seconds"]]
    warm_s = sum(warm) / len(warm)
    report = {
        "shot": shot,
        "gpu": dev["name"],
        "vram_total_gb": round(dev["vram_total"] / 1e9, 1),
        "comfyui": stats["system"].get("comfyui_version"),
        "container_uptime_at_start_s": uptime_at_start,
        "runs": runs,
        "summary": {
            "output_seconds": round(out_s, 2),
            "warm_gpu_s_per_output_s": round(warm_s / out_s, 1),
            "warm_nt_per_output_s": round(warm_s / out_s / 3600 * RATE_NT_PER_H, 3),
            "cold_extra_s": round(runs[0]["seconds"] - warm_s, 1) if runs[0]["cold"] and len(runs) > 1 else None,
        },
    }
    dest = args.output_dir / "poc" / f"{shot['id']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"報告：{dest}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shots", type=Path, nargs="+", help="一個或多個鏡頭規格；同一次開機依序跑完，只有第一個是冷啟動")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--output-dir", type=Path, default=Path(os.environ.get("OUTPUT_DIR", "/vault/outputs")))
    ap.add_argument("--dry-run", action="store_true", help="只驗證工作流與模型檔，送出後立刻取消")
    ap.add_argument("--warm", action="store_true", help="模型已經載入過（同一次開機的第二批），不標冷啟動")
    ap.add_argument("--wait", type=float, default=600, help="等 ComfyUI 起來的秒數")
    args = ap.parse_args()

    shots = [json.loads(p.read_text()) for p in args.shots]
    uptime_at_start = container_uptime_s()
    stats = wait_for_comfy(args.comfy, args.wait)
    dev = stats["devices"][0]
    print(f"ComfyUI {stats['system'].get('comfyui_version')} · {dev['name']} · "
          f"VRAM {dev['vram_total'] / 1e9:.1f} GB · 容器已開機 {container_uptime_s()} s")

    client_id = uuid.uuid4().hex
    # 全部鏡頭先組好、先驗模型，GPU 開始算之前就擋掉規格錯誤
    firsts = [build_prompt(shot, shot["seeds"][0]) for shot in shots]
    missing = sorted({m for wf in firsts for m in check_models(args.comfy, wf)})
    if missing:
        raise SystemExit("缺少模型檔（先下載到 /vault/models）：\n  " + "\n  ".join(missing))

    if args.dry_run:
        pid = call(args.comfy, "/prompt", {"prompt": firsts[0], "client_id": client_id})["prompt_id"]
        call(args.comfy, "/queue", {"delete": [pid]})
        call(args.comfy, "/interrupt", {})
        print(f"✅ 工作流驗證通過（{len(shots)} 個鏡頭；prompt {pid} 已取消）")
        return

    for i, shot in enumerate(shots):
        run_shot(args, shot, stats, client_id, cold_first=i == 0 and not args.warm, uptime_at_start=uptime_at_start)
    print("⚠️ 跑完記得 stop / delete 執行個體，閒置也在計費。")


if __name__ == "__main__":
    sys.exit(main())
