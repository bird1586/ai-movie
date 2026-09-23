import time

from fastapi.testclient import TestClient

from app.main import app

SCRIPT = "峽谷全景，風雪。凱倫斯低聲說：「我們不能再往前走了，森林裡有東西在看著我們。」獸人從兩側跳出伏擊！"


def _client():
    return TestClient(app)


def test_project_storyboard_gate_flow():
    with _client() as c:
        p = c.post("/api/projects", json={"name": "測試", "target_s": 15}).json()
        pid = p["id"]
        assert p["step"] == 1 and p["budget"]["level"] == "ok"

        shots = c.post(f"/api/projects/{pid}/storyboard:parse", json={"script": SCRIPT}).json()
        assert [s["track"] for s in shots] == ["B", "B", "A"]
        assert shots[1]["lip_sync"] and shots[1]["duration"]["frames"] % 4 == 1

        r = c.post(f"/api/projects/{pid}/gates/G2a:approve")
        assert r.status_code == 409

        r = c.post(f"/api/projects/{pid}/gates/G1:approve").json()
        assert r["project"]["step"] == 2

        r = c.post(f"/api/projects/{pid}/storyboard:parse", json={"script": SCRIPT})
        assert r.status_code == 409


def test_budget_ledger_and_whatif():
    with _client() as c:
        pid = c.post("/api/projects", json={"name": "b", "budget_cap": 100}).json()["id"]
        r = c.post(f"/api/projects/{pid}/ledger", json={"category": "asset", "amount_nt": 85}).json()
        assert r["level"] == "warn"
        b = c.get(f"/api/projects/{pid}/budget").json()
        assert b["by_category"] == {"asset": 85}
        assert c.get(f"/api/projects/{pid}/budget:whatif").status_code == 200


def test_mock_job_runs_to_done():
    with _client() as c:
        pid = c.post("/api/projects", json={"name": "j"}).json()["id"]
        with c.websocket_connect(f"/ws/projects/{pid}") as ws:
            job = c.post(f"/api/projects/{pid}/jobs", json={"kind": "tts"}).json()
            assert job["status"] == "QUEUED"
            events = []
            deadline = time.time() + 10
            while time.time() < deadline:
                ev = ws.receive_json()
                events.append(ev["type"])
                if ev["type"] == "job.done":
                    break
        assert "job.progress" in events and events[-1] == "job.done"
        done = c.get(f"/api/projects/{pid}/jobs").json()[0]
        assert done["status"] == "DONE" and done["verdict"]["mock"] is True


def test_unknown_job_kind():
    with _client() as c:
        pid = c.post("/api/projects", json={"name": "x"}).json()["id"]
        assert c.post(f"/api/projects/{pid}/jobs", json={"kind": "nope"}).status_code == 422
