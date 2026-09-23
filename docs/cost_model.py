"""可行性評估 §3 的成本試算。所有輸入皆取自 開發計劃書.md §6，修正項以常數標出。

執行：python3 docs/cost_model.py
"""

RATE_4090 = 16.18  # NT$/h，GPUTW 官方
PREPROD_S_PER_S = 9.62  # 前製（Blender 阻擋）每 1 秒成片的 GPU 秒數，沿用計劃書

# 計劃書 §6.3 V2a 每鏡耗時（秒）與鏡頭長度（秒）
TRACK_A = {"shot_s": 4.0, "total": 695, "draft_denoise": 221, "refine": 246}
TRACK_B = {"shot_s": 3.67, "total": 181, "draft_denoise": 14, "refine": 31}
MIX_A = 0.30

# A14B 在 720p、去 CFG 下的單步耗時（秒/步，81 幀）
# 由 Wan 官方 A100 實測 2810.9 s / 40 步（含 CFG）÷ 2 推得；計劃書假設 4090 ≈ A100
A14B_S_PER_STEP_NO_CFG = 2810.9 / 40 / 2


def gpu_s_per_video_s(a, b):
    return MIX_A * a["total"] / a["shot_s"] + (1 - MIX_A) * b["total"] / b["shot_s"]


def track_b_on_a14b():
    """Track B 首尾幀插值若必須用 A14B（5B 無 FLF 支援時）的每鏡耗時。"""
    frames_ratio = 61 / 81  # 3.67 s 鏡頭 ≈ 61 幀 vs 5 s 的 81 幀
    draft = 6 * A14B_S_PER_STEP_NO_CFG * frames_ratio
    refine = draft * TRACK_A["refine"] / TRACK_A["draft_denoise"]
    total = TRACK_B["total"] - TRACK_B["draft_denoise"] - TRACK_B["refine"] + draft + refine
    return {**TRACK_B, "draft_denoise": draft, "refine": refine, "total": total}


def minutes_for_budget(budget, asset_h, overhead_nt, s_per_s):
    left_h = (budget - overhead_nt) / RATE_4090 - asset_h
    return max(left_h, 0) * 3600 / s_per_s / 60


def main():
    s1 = gpu_s_per_video_s(TRACK_A, TRACK_B) + PREPROD_S_PER_S
    s2 = gpu_s_per_video_s(TRACK_A, track_b_on_a14b()) + PREPROD_S_PER_S
    scenarios = {"S1 計劃書原設（B 軌小模型）": s1, "S2 B 軌也用 A14B": s2}

    print(f"A14B 720p 去 CFG 單步：{A14B_S_PER_STEP_NO_CFG:.1f} s/步")
    print(f"Track B on A14B 每鏡：{track_b_on_a14b()['total']:.0f} s\n")
    for name, sps in scenarios.items():
        print(f"{name}: {sps:.1f} GPU-s/成片秒 → {3600 / sps:.1f} s/h → NT${sps / 3600 * RATE_4090:.3f}/s")

    print("\n1000 NTD 可產分鐘數（開機+閒置開銷 NT$40）")
    print("LoRA 時數/個  資產數  資產 GPU-h   S1     S2")
    for lora_h in (3.0, 4.0, 5.0):
        for n_assets in (11, 6):  # 11 = 計劃書 14 個扣掉 3 條聲線（不需訓練）；6 = 精簡版
            asset_h = n_assets * lora_h
            m1 = minutes_for_budget(1000, asset_h, 40, s1)
            m2 = minutes_for_budget(1000, asset_h, 40, s2)
            print(f"{lora_h:>10.1f}  {n_assets:>6}  {asset_h:>9.0f}  {m1:5.1f}  {m2:5.1f}")


if __name__ == "__main__":
    main()
