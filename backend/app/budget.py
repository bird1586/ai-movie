"""預算記帳與試算（UI.md §7；費率與係數見 docs/可行性評估.md §3）。"""

from dataclasses import dataclass

RATE_4090 = 16.18  # NT$/h
PREPROD_NT_PER_S = 9.62 / 3600 * RATE_4090  # 前製（灰模）每 1 秒成片
STARTUP_OVERHEAD_NT = 40.0  # 開機拉映像 + 載入模型 + 閒置的保守預留

# 每 1 秒 2K/60fps 成片的推論 + 後製成本（計劃書 §6.5，C2 口徑）
STRATEGY_NT_PER_S = {
    "V1_fast": 0.296,
    "V2a_refine": 0.389,
    "V2d_adaptive": 0.492,
    "V2c_hero": 0.826,
}
# Track B 也必須用 A14B 時的倍率（S2 / S1 = 0.682 / 0.433）
TRACK_B_A14B_FACTOR = 0.682 / 0.433
LIPSYNC_SHARE = 0.10  # 口型同步佔總成本 8–12%，取中間值

WARN_LEVELS = ((1.0, "hold"), (0.9, "alert"), (0.8, "warn"))


@dataclass
class EstimateInput:
    seconds: float
    strategy: str = "V2a_refine"
    new_assets: int = 0
    lora_hours: float = 3.0
    lip_sync: bool = True
    track_b_a14b: bool = False


def estimate(inp: EstimateInput) -> dict[str, float]:
    per_s = STRATEGY_NT_PER_S[inp.strategy] + PREPROD_NT_PER_S
    if inp.track_b_a14b:
        per_s *= TRACK_B_A14B_FACTOR
    if not inp.lip_sync:
        per_s *= 1 - LIPSYNC_SHARE
    production = inp.seconds * per_s
    assets = inp.new_assets * inp.lora_hours * RATE_4090
    total = production + assets + STARTUP_OVERHEAD_NT
    return {
        "production": round(production, 1),
        "assets": round(assets, 1),
        "overhead": STARTUP_OVERHEAD_NT,
        "total": round(total, 1),
    }


def what_if(base: EstimateInput) -> list[dict]:
    """UI.md §7「如果…會怎樣」：每個替代方案相對目前估算省下（負值為多花）多少。"""
    base_total = estimate(base)["total"]
    variants = [
        ("改用「省錢快速」", {"strategy": "V1_fast"}),
        ("改用「劇情級」", {"strategy": "V2c_hero"}),
        ("關掉口型", {"lip_sync": False}),
        ("資產從資產庫匯入", {"new_assets": 0}),
    ]
    out = []
    for label, change in variants:
        alt = EstimateInput(**{**base.__dict__, **change})
        if alt == base:
            continue
        out.append({"label": label, "saving": round(base_total - estimate(alt)["total"], 1)})
    return out


def summarize(used: float, cap: float, projected: float | None = None) -> dict:
    ratio = used / cap if cap else 1.0
    level = next((name for threshold, name in WARN_LEVELS if ratio >= threshold), "ok")
    return {
        "used": round(used, 2),
        "cap": cap,
        "ratio": round(ratio, 4),
        "level": level,
        "projected": projected,
        "within_budget": projected is None or projected <= cap,
    }
