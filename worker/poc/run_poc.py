"""GPUtw PoC：把一個鏡頭規格丟給容器內的 ComfyUI，量測每次生成的耗時、VRAM 與成本。

只用標準函式庫，容器內直接跑：
    python /opt/poc/run_poc.py /opt/poc/shots/opening_pan.json
不花 GPU 也能先檢查工作流（ComfyUI 會做完整驗證，接著立刻取消）：
    python run_poc.py shots/opening_pan.json --dry-run
可以一次給多個鏡頭，同一次開機依序跑完（只載入一次模型）；所有工作一開始就送進 ComfyUI 佇列，
GPU 算下一支時本機同時下載上一支。--max-seeds 1 每個鏡頭只跑一個版本；--chain 讓後一鏡從前一鏡最後一格接下去。
也可以在本機跑，對遠端 ComfyUI（例如 GPUtw 官方範本的 Web UI）送工作，影片用 /view 下載回來：
    COMFY_COOKIE='…' python run_poc.py shots/opening_pan.json --comfy https://8080-<id>.gputw.ai --output-dir ../../data

每個 seed 生成一次；第一次含模型載入（冷啟動），之後是熱機時間，兩者分開記錄。
結果寫到 <輸出目錄>/poc/<第一個 shot id>[+N]-<時間>.json，影片在同一個目錄。
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
# 每個鏡頭各自一份的節點；其餘（模型載入）在 --chain 時共用
PER_SHOT_NODES = ("6", "7", "55", "3", "8", "57", "58")
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
    """每秒讀一次 /system_stats，記錄 VRAM 使用峰值；take() 取出並歸零，每支影片各算各的。"""

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

    def take(self) -> float:
        peak, self.peak_gb = self.peak_gb, 0.0
        return round(peak, 2)


def build_chain(shots: list[dict], seeds: list[int]) -> dict:
    """多個鏡頭接成一個工作流：後一鏡用前一鏡最後一格當 start_image，接點不會跳。
    模型載入節點共用；前一鏡的畫格直接在 GPU 機器上傳給下一鏡，不經過本機。"""
    if len({(s["workflow"], s["width"], s["height"], s["fps"]) for s in shots}) != 1:
        raise SystemExit("--chain 的鏡頭必須用同一個工作流、同樣的寬高與 fps")
    wf = {}
    for i, (shot, seed) in enumerate(zip(shots, seeds)):
        part = build_prompt(shot, seed)
        part[SAVE_NODE]["inputs"]["filename_prefix"] += "_chained"
        p = f"c{i}_"
        for nid, node in part.items():
            if nid not in PER_SHOT_NODES:
                wf.setdefault(nid, node)
                continue
            node["inputs"] = {k: [p + v[0], v[1]] if isinstance(v, list) and v[0] in PER_SHOT_NODES else v
                              for k, v in node["inputs"].items()}
            wf[p + nid] = node
        if i:
            wf[p + "last"] = {"class_type": "ImageFromBatch",
                              "inputs": {"image": [f"c{i - 1}_8", 0], "batch_index": shots[i - 1]["frames"] - 1, "length": 1}}
            wf[p + "55"]["inputs"]["start_image"] = [p + "last", 0]
    return wf


def plan_jobs(shots: list[dict], max_seeds: int | None, chain: bool) -> list[dict]:
    """每個 job 是一次 /prompt。一般模式：每個鏡頭 × 每個 seed 各一個；--chain：第 i 個版本用每個鏡頭的第 i 個 seed。"""
    if chain:
        n = min(len(s["seeds"]) for s in shots)
        return [{"shots": shots, "seeds": [s["seeds"][i] for s in shots],
                 "save_nodes": [f"c{j}_{SAVE_NODE}" for j in range(len(shots))],
                 "wf": build_chain(shots, [s["seeds"][i] for s in shots])}
                for i in range(min(n, max_seeds or n))]
    return [{"shots": [shot], "seeds": [seed], "save_nodes": [SAVE_NODE], "wf": build_prompt(shot, seed)}
            for shot in shots for seed in shot["seeds"][:max_seeds]]


def wait_done(base: str, pid: str) -> dict:
    while True:
        hist = call(base, f"/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed") is not None:
            break
        time.sleep(2)
    status = hist["status"]
    if status.get("status_str") != "success":
        errors = [m for m in status.get("messages", []) if m[0] == "execution_error"]
        raise RuntimeError(f"生成失敗：{json.dumps(errors, ensure_ascii=False)[:2000]}")
    return hist


def exec_seconds(hist: dict) -> float | None:
    """ComfyUI 自己記的開始／結束時間；排隊等待不算在內。"""
    ts = {m[0]: m[1].get("timestamp") for m in hist["status"].get("messages", []) if isinstance(m[1], dict)}
    if ts.get("execution_start") and ts.get("execution_success"):
        return round((ts["execution_success"] - ts["execution_start"]) / 1000, 1)
    return None


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


def concat_chain(paths: list[Path], fps: int, dest: Path) -> None:
    """後一鏡的第 0 格就是前一鏡的最後一格，接的時候拿掉，不然每個接點都會停一格。"""
    inputs = [a for p in paths for a in ("-i", str(p))]
    parts = "".join(f"[{i}:v]{'trim=start_frame=1,setpts=PTS-STARTPTS,' if i else ''}format=yuv420p[v{i}];"
                    for i in range(len(paths)))
    graph = parts + "".join(f"[v{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-crf", "18", "-r", str(fps), "-movflags", "+faststart", str(dest)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shots", type=Path, nargs="+", help="一個或多個鏡頭規格；同一次開機依序跑完，只有第一個是冷啟動")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--output-dir", type=Path, default=Path(os.environ.get("OUTPUT_DIR", "/vault/outputs")))
    ap.add_argument("--max-seeds", type=int, help="每個鏡頭只跑前 N 個 seed（畫面方向確定後用 1，費用減半）")
    ap.add_argument("--chain", action="store_true", help="鏡頭依序接成一段：後一鏡從前一鏡最後一格開始，並在本機接成一支影片")
    ap.add_argument("--check", action="store_true", help="只在本機組工作流、檢查規格，不連 ComfyUI")
    ap.add_argument("--dry-run", action="store_true", help="只驗證工作流與模型檔，送出後立刻取消")
    ap.add_argument("--warm", action="store_true", help="模型已經載入過（同一次開機的第二批），不標冷啟動")
    ap.add_argument("--wait", type=float, default=600, help="等 ComfyUI 起來的秒數")
    args = ap.parse_args()

    shots = [json.loads(p.read_text()) for p in args.shots]
    # 全部 job 先組好，GPU 開始算之前就擋掉規格錯誤
    jobs = plan_jobs(shots, args.max_seeds, args.chain)
    out_s = sum(s["frames"] for s in shots if s in jobs[0]["shots"]) / shots[0]["fps"]
    print(f"{len(jobs)} 次生成：" + "、".join(
        "+".join(f"{s['id']}#{seed}" for s, seed in zip(j["shots"], j["seeds"])) for j in jobs), flush=True)
    if args.check:
        return

    uptime_at_start = container_uptime_s()
    stats = wait_for_comfy(args.comfy, args.wait)
    dev = stats["devices"][0]
    print(f"ComfyUI {stats['system'].get('comfyui_version')} · {dev['name']} · "
          f"VRAM {dev['vram_total'] / 1e9:.1f} GB · 容器已開機 {container_uptime_s()} s")
    missing = sorted({m for j in jobs for m in check_models(args.comfy, j["wf"])})
    if missing:
        raise SystemExit("缺少模型檔（先下載到 /vault/models）：\n  " + "\n  ".join(missing))

    client_id = uuid.uuid4().hex
    # 一開始就全部送進佇列：GPU 算下一支的同時，本機下載上一支，中間不空等
    t0 = time.monotonic()
    for j in jobs:
        j["pid"] = call(args.comfy, "/prompt", {"prompt": j["wf"], "client_id": client_id})["prompt_id"]
    if args.dry_run:
        call(args.comfy, "/queue", {"delete": [j["pid"] for j in jobs]})
        call(args.comfy, "/interrupt", {})
        print(f"✅ 工作流驗證通過（{len(jobs)} 次生成已取消）")
        return

    sampler = VramSampler(args.comfy)
    sampler.start()
    runs, last_done = [], t0
    for i, j in enumerate(jobs):
        cold = i == 0 and not args.warm
        label = "+".join(f"{s['id']} seed {seed}" for s, seed in zip(j["shots"], j["seeds"]))
        print(f"▶ {label}：{'冷啟動（含載入模型）' if cold else '熱機'} …", flush=True)
        hist = wait_done(args.comfy, j["pid"])
        done = time.monotonic()
        seconds = exec_seconds(hist) or round(done - last_done, 1)
        last_done = done
        files = []
        for node in j["save_nodes"]:
            out = hist["outputs"][node]["images"][0]
            files.append(str(Path(out.get("subfolder", "")) / out["filename"]))
            fetch_output(args.comfy, out, args.output_dir / files[-1])
        r = {"prompt_id": j["pid"], "shots": [s["id"] for s in j["shots"]], "seeds": j["seeds"], "cold": cold,
             "seconds": seconds, "vram_peak_gb": sampler.take(), "files": files,
             "cost_nt": round(seconds / 3600 * RATE_NT_PER_H, 2)}
        if args.chain:
            dest = args.output_dir / "poc" / f"{'+'.join(r['shots'])}_s{j['seeds'][0]}.mp4"
            concat_chain([args.output_dir / f for f in files], shots[0]["fps"], dest)
            r["joined"] = str(dest.relative_to(args.output_dir))
        r["video"] = probe(args.output_dir / (r.get("joined") or files[0]))
        runs.append(r)
        print(f"  {seconds} s · VRAM 峰值 {r['vram_peak_gb']} GB · NT${r['cost_nt']} · {r.get('joined') or ', '.join(files)}",
              flush=True)
    sampler.stop.set()

    warm = [r["seconds"] for r in runs if not r["cold"]] or [runs[0]["seconds"]]
    warm_s = sum(warm) / len(warm)
    report = {
        "shots": shots,
        "chain": args.chain,
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
    dest = args.output_dir / "poc" / f"{shots[0]['id']}{f'+{len(shots) - 1}' if len(shots) > 1 else ''}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"報告：{dest}\n⚠️ 跑完記得 stop / delete 執行個體，閒置也在計費。", flush=True)


if __name__ == "__main__":
    sys.exit(main())
