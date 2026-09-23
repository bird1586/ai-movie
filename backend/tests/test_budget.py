from app import budget
from app.budget import EstimateInput


def test_matches_plan_per_second_cost():
    # 計劃書 V2a 含前製 NT$0.433/s
    e = budget.estimate(EstimateInput(seconds=1000))
    assert abs(e["production"] - 433) < 1


def test_track_b_a14b_scenario():
    e = budget.estimate(EstimateInput(seconds=1000, track_b_a14b=True))
    assert abs(e["production"] - 682) < 2


def test_assets_and_overhead():
    e = budget.estimate(EstimateInput(seconds=0, new_assets=2, lora_hours=3))
    assert e["assets"] == round(2 * 3 * 16.18, 1)
    assert e["total"] == e["assets"] + budget.STARTUP_OVERHEAD_NT


def test_warn_levels():
    assert budget.summarize(500, 1000)["level"] == "ok"
    assert budget.summarize(800, 1000)["level"] == "warn"
    assert budget.summarize(950, 1000)["level"] == "alert"
    assert budget.summarize(1000, 1000)["level"] == "hold"
    assert budget.summarize(10, 1000, projected=1200)["within_budget"] is False


def test_what_if_skips_no_op_and_signs():
    items = {i["label"]: i["saving"] for i in budget.what_if(EstimateInput(seconds=60, new_assets=0))}
    assert "資產從資產庫匯入" not in items
    assert items["改用「省錢快速」"] > 0 > items["改用「劇情級」"]
