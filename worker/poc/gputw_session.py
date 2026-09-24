"""一次付費開機跑完一批鏡頭：挑最便宜的機器 → 官方 ComfyUI 範本 → 等 RUNNING → 拿 Web UI cookie
→ 本機跑 run_poc.py（影片下載到本機）→ 無論成功失敗都 stop。

只用標準函式庫，在本機（不是 GPU 機器）執行：
    python gputw_session.py shots/xianxia_clash.json shots/xianxia_flight.json
    python gputw_session.py shots/xianxia_clash.json shots/xianxia_flight_cont.json --chain --max-seeds 1
模型要先在 /vault/models/<類別>/（見 README.md）。API key 讀 ~/.config/gputw/key（權限 600），
這支程式不會印出 key。
"""

import argparse
import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.gputw.ai/api"
UA = "ai-movie-poc/1.0"
KEY = Path(os.environ.get("GPUTW_KEY_FILE", Path.home() / ".config/gputw/key")).read_text().strip()
TWD_PER_USD = float(os.environ.get("TWD_PER_USD", 31.5))  # 由第 1 次 PoC 實際扣款反推


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {KEY}", "User-Agent": UA, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from None
    return d.get("data", d) if isinstance(d, dict) else d


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_node(gpu_names: list[str], max_usd: float) -> dict:
    """依偏好順序找有空的機器，同一型號挑最便宜。"""
    catalog = api("GET", "/gpus/active")
    for name in gpu_names:
        for gpu in (g for g in catalog if name.lower() in g["name"].lower()):
            nodes = [n for n in api("GET", f"/nodes/available?catalogId={gpu['id']}")
                     if n["availableGpus"] > 0 and not n["queueDepth"] and n["arch"] == "amd64"
                     and n["hourlyRate"] <= max_usd]
            if nodes:
                return min(nodes, key=lambda n: n["hourlyRate"])
    raise SystemExit(f"沒有空機器（{', '.join(gpu_names)}，≤ US${max_usd}/h）")


def comfy_template() -> dict:
    return next(t for t in api("GET", "/templates") if t["name"] == "ComfyUI" and "amd64" in t["architectures"])


def web_cookie(instance_id: str, port: int) -> str:
    """access-token 網址 90 秒內換成 cookie，之後呼叫 ComfyUI API 都帶這個 cookie。"""
    url = api("POST", f"/instances/{instance_id}/access-token", {"port": port, "path": ""})["url"]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()
    if not jar:
        raise RuntimeError("access-token 換不到 cookie")
    return "; ".join(f"{c.name}={c.value}" for c in jar)


def wait_running(instance_id: str, timeout_s: float) -> float:
    t0 = time.monotonic()
    last = None
    while True:
        s = api("GET", f"/instances/{instance_id}/status")
        state = (s["status"], s.get("deployPhase"), s.get("ready"))
        if state != last:
            log(f"狀態 {state[0]} / {state[1]} / ready={state[2]}")
            last = state
        if s["status"] == "RUNNING" and s.get("ready"):
            return time.monotonic() - t0
        if s["status"] in ("FAILED", "INSUFFICIENT_FUNDS"):
            raise RuntimeError(f"部署失敗：{s['status']} {s.get('failureReason')}")
        if time.monotonic() - t0 > timeout_s:
            raise RuntimeError(f"{timeout_s:.0f}s 內沒有 RUNNING（停損）")
        time.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shots", type=Path, nargs="+")
    ap.add_argument("--gpu", action="append", help="偏好的 GPU 型號（可多次，依序嘗試）；預設 RTX 4090 → RTX 5090")
    ap.add_argument("--max-usd", type=float, default=0.8, help="每小時上限（美元）")
    ap.add_argument("--boot-timeout", type=float, default=900, help="開機停損秒數")
    ap.add_argument("--output-dir", type=Path, default=HERE.parents[1] / "data")
    ap.add_argument("--max-seeds", type=int, help="傳給 run_poc.py：每個鏡頭只跑前 N 個 seed")
    ap.add_argument("--chain", action="store_true", help="傳給 run_poc.py：鏡頭接成一段，後一鏡從前一鏡最後一格開始")
    ap.add_argument("--yes", action="store_true", help="不再確認直接開機（使用者已同意這次花費）")
    args = ap.parse_args()

    passthrough = [*(["--max-seeds", str(args.max_seeds)] if args.max_seeds else []), *(["--chain"] if args.chain else [])]
    # 開機前先在本機組好全部工作流、驗鏡頭規格，錯了就不花錢
    subprocess.run([sys.executable, str(HERE / "run_poc.py"), *map(str, args.shots), *passthrough, "--check"], check=True)

    node = pick_node(args.gpu or ["RTX 4090", "RTX 5090"], args.max_usd)
    tmpl = comfy_template()
    rate_nt = node["hourlyRate"] * TWD_PER_USD
    balance0 = api("GET", "/vault/stats")["balanceNtd"]
    log(f"機器 {node['gpuModel']} {node['id'][:8]} US${node['hourlyRate']}/h（≈ NT${rate_nt:.1f}/h）；"
        f"範本 {tmpl['dockerImage']}；餘額 NT${balance0:.2f}")
    if not args.yes and input("開機？[y/N] ").strip().lower() != "y":
        return

    # SIGTERM（例如背景工作被中止）也要走到 finally 去 stop
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    session = {"node": node, "template": tmpl["dockerImage"], "shots": [str(p) for p in args.shots]}
    t0 = time.monotonic()
    inst = api("POST", "/instances/create", {"nodeId": node["id"], "templateId": tmpl["id"]})
    iid = inst["id"]
    log(f"已建立 {iid}，開始計費")
    try:
        session["boot_s"] = round(wait_running(iid, args.boot_timeout), 1)
        log(f"RUNNING（開機 {session['boot_s']} s）")
        port = tmpl.get("webUiPort") or 8080
        env = os.environ | {"COMFY_COOKIE": web_cookie(iid, port), "RATE_NT_PER_H": f"{rate_nt:.2f}"}
        t1 = time.monotonic()
        subprocess.run([sys.executable, "-u", str(HERE / "run_poc.py"), *map(str, args.shots),
                        *passthrough, "--comfy", f"https://{port}-{iid}.gputw.ai", "--output-dir", str(args.output_dir)],
                       env=env, check=True)
        session["generate_s"] = round(time.monotonic() - t1, 1)
    finally:
        api("POST", "/instances/stop", {"instanceId": iid})
        session["billed_s"] = round(time.monotonic() - t0, 1)
        log(f"已 stop {iid}（計費約 {session['billed_s']:.0f} s）")
        time.sleep(5)  # 扣款稍晚才入帳
        session["cost_nt"] = round(balance0 - api("GET", "/vault/stats")["balanceNtd"], 2)
        session["instance_id"] = iid
        dest = args.output_dir / "poc" / f"session-{time.strftime('%Y%m%d-%H%M%S')}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(session, ensure_ascii=False, indent=2))
        log(f"實際扣款 NT${session['cost_nt']}（可能還沒全部入帳）；紀錄：{dest}")


if __name__ == "__main__":
    main()
