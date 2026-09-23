"""控制平面 API（UI.md §10.2）。"""

import json
import re
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import budget, db, gates, jobs
from .shotspec import Dialogue, RenderModel, ShotSpec, VoiceProfile, lock_duration


class Hub:
    """每個專案一組 WebSocket 連線，用來推送 job / gate / budget 事件。"""

    def __init__(self):
        self.clients: dict[str, set[WebSocket]] = defaultdict(set)

    async def emit(self, project_id: str, event: dict) -> None:
        for ws in list(self.clients[project_id]):
            try:
                await ws.send_json(event)
            except Exception:
                self.clients[project_id].discard(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    runner = jobs.JobRunner(jobs.MockExecutor(), hub.emit)
    runner.start()
    yield
    runner.shutdown()


app = FastAPI(title="ai-movie 控制平面", lifespan=lifespan)


# ── 專案 ────────────────────────────────────────────────────────────────

class ProjectSettings(BaseModel):
    resolution: str = Field("2K", pattern="^(1080p|2K|4K)$")
    fps: int = Field(60, ge=24, le=60)
    strategy: str = Field("V2a_refine", pattern="^(V1_fast|V2a_refine|V2d_adaptive|V2c_hero)$")
    lip_sync: bool = True
    new_assets: int = Field(0, ge=0)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_s: float = Field(60, gt=0, le=3600)
    budget_cap: float = Field(1000, gt=0)
    settings: ProjectSettings = ProjectSettings()


def _load_project(pid: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到專案")
    return db.row_to_dict(row, ("settings", "gates"))


def _save_gates(pid: str, state: dict) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET gates = ? WHERE id = ?", (json.dumps(state), pid))


def _used(pid: str) -> float:
    with db.connect() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(amount_nt), 0) FROM ledger WHERE project_id = ?", (pid,)
        ).fetchone()[0]


def _estimate_input(p: dict) -> budget.EstimateInput:
    s = p["settings"]
    return budget.EstimateInput(
        seconds=p["target_s"], strategy=s["strategy"], new_assets=s["new_assets"], lip_sync=s["lip_sync"]
    )


def _view(p: dict) -> dict:
    state = {k: gates.GateStatus(v) for k, v in p["gates"].items()}
    est = budget.estimate(_estimate_input(p))
    return {
        **p,
        "step": gates.current_step(state),
        "gate_list": [
            {"id": g.id, "step": g.step, "name": g.name, "rework_cost": g.rework_cost, "status": state[g.id]}
            for g in gates.GATES
        ],
        "estimate": est,
        "budget": budget.summarize(_used(p["id"]), p["budget_cap"], est["total"]),
    }


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/projects")
def list_projects():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [_view(db.row_to_dict(r, ("settings", "gates"))) for r in rows]


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    pid = uuid.uuid4().hex[:10]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, target_s, budget_cap, settings, gates) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, body.name, body.target_s, body.budget_cap, body.settings.model_dump_json(),
             json.dumps(gates.initial_state())),
        )
    return _view(_load_project(pid))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _view(_load_project(pid))


# ── 分鏡（S1）───────────────────────────────────────────────────────────

class ParseIn(BaseModel):
    script: str = Field(min_length=1)
    model: RenderModel = RenderModel.WAN22_A14B


FAST_WORDS = ("伏擊", "跳", "衝", "爆", "打", "揮", "追", "跑", "斬", "拔")
DIALOGUE_RE = re.compile(r"「([^」]+)」")


def _split_sentences(script: str) -> list[str]:
    """依句號/驚嘆號/問號/換行斷句，但「」內的標點不斷。"""
    out, buf, depth = [], "", 0
    for ch in script:
        if ch == "\n" and depth == 0:
            out.append(buf)
            buf = ""
            continue
        buf += ch
        depth += (ch == "「") - (ch == "」")
        quote_ends_sentence = ch == "」" and buf[-2:-1] in tuple("。！？!?")
        if depth == 0 and (ch in "。！？!?" or quote_ends_sentence):
            out.append(buf)
            buf = ""
    out.append(buf)
    return [s.strip() for s in out if s.strip()]


def _mock_parse(script: str, model: RenderModel) -> list[ShotSpec]:
    """LLM 拆分鏡的替身：一句一鏡、「」視為對白、動作詞判定 Track A。

    對白鏡頭先用每秒 4 字估時長，G4 配音後會由實際音檔長度重新鎖定。
    """
    sentences = _split_sentences(script)
    shots = []
    for i, text in enumerate(sentences, 1):
        m = DIALOGUE_RE.search(text)
        track = "A" if any(w in text for w in FAST_WORDS) else "B"
        dialogue = None
        if m:
            duration = lock_duration(model, audio_s=len(m.group(1)) / 4)
            duration.locked = False  # 估計值，還沒有真的音檔
            dialogue = Dialogue(character_id="unassigned", text=m.group(1),
                                voice_profile=VoiceProfile(timbre_ref=""))
        else:
            duration = lock_duration(model, target_s=3.0 if track == "B" else 2.0)
        shots.append(ShotSpec(shot_id=f"shot_{i:03d}", description=text, track=track,
                              lip_sync=dialogue is not None, duration=duration, dialogue=dialogue,
                              render={"model": model}))
    return shots


@app.post("/api/projects/{pid}/storyboard:parse")
async def parse_storyboard(pid: str, body: ParseIn):
    p = _load_project(pid)
    state = {k: gates.GateStatus(v) for k, v in p["gates"].items()}
    if state["G1"] == gates.GateStatus.APPROVED:
        raise HTTPException(409, "分鏡表已確認，要先回頭（reopen G1）才能重新拆解")
    shots = _mock_parse(body.script, body.model)
    with db.connect() as conn:
        conn.execute("DELETE FROM shots WHERE project_id = ?", (pid,))
        conn.executemany(
            "INSERT INTO shots (project_id, shot_id, seq, spec) VALUES (?, ?, ?, ?)",
            [(pid, s.shot_id, i, s.model_dump_json()) for i, s in enumerate(shots)],
        )
    gates.mark_ready(state, "G1")
    _save_gates(pid, state)
    await hub.emit(pid, {"type": "gate.ready", "gate": "G1"})
    return list_shots(pid)


@app.get("/api/projects/{pid}/shots")
def list_shots(pid: str):
    _load_project(pid)
    with db.connect() as conn:
        rows = conn.execute("SELECT spec FROM shots WHERE project_id = ? ORDER BY seq", (pid,)).fetchall()
    out = []
    for r in rows:
        spec = ShotSpec.model_validate_json(r["spec"])
        out.append({**spec.model_dump(mode="json"), "needs_split": spec.duration.needs_split})
    return out


# ── 門檻 ────────────────────────────────────────────────────────────────

class GateAction(BaseModel):
    feedback: str | None = None


async def _gate_action(pid: str, gid: str, action) -> dict:
    p = _load_project(pid)
    state = {k: gates.GateStatus(v) for k, v in p["gates"].items()}
    try:
        extra = action(state, gid)
    except gates.GateError as e:
        raise HTTPException(409, str(e))
    _save_gates(pid, state)
    await hub.emit(pid, {"type": "gate.changed", "gate": gid, "status": state[gid]})
    return {"project": _view(_load_project(pid)), "invalidated": extra or []}


@app.post("/api/projects/{pid}/gates/{gid}:ready")
async def gate_ready(pid: str, gid: str):
    return await _gate_action(pid, gid, gates.mark_ready)


@app.post("/api/projects/{pid}/gates/{gid}:approve")
async def gate_approve(pid: str, gid: str, body: GateAction | None = None):
    return await _gate_action(pid, gid, gates.approve)


@app.post("/api/projects/{pid}/gates/{gid}:reject")
async def gate_reject(pid: str, gid: str, body: GateAction | None = None):
    return await _gate_action(pid, gid, gates.reject)


@app.post("/api/projects/{pid}/gates/{gid}:reopen")
async def gate_reopen(pid: str, gid: str):
    return await _gate_action(pid, gid, gates.reopen)


# ── 預算 ────────────────────────────────────────────────────────────────

class Charge(BaseModel):
    category: str = Field(pattern="^(asset|tts|block|draft|render|post|overhead)$")
    amount_nt: float = Field(ge=0)
    note: str = ""


@app.get("/api/projects/{pid}/budget")
def get_budget(pid: str):
    p = _load_project(pid)
    est = budget.estimate(_estimate_input(p))
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT category, SUM(amount_nt) AS amount FROM ledger WHERE project_id = ? GROUP BY category",
            (pid,),
        ).fetchall()
    return {**budget.summarize(_used(pid), p["budget_cap"], est["total"]),
            "estimate": est, "by_category": {r["category"]: r["amount"] for r in rows}}


@app.get("/api/projects/{pid}/budget:whatif")
def get_whatif(pid: str):
    return budget.what_if(_estimate_input(_load_project(pid)))


@app.post("/api/projects/{pid}/ledger", status_code=201)
async def add_charge(pid: str, body: Charge):
    p = _load_project(pid)
    with db.connect() as conn:
        conn.execute("INSERT INTO ledger (project_id, category, amount_nt, note) VALUES (?, ?, ?, ?)",
                     (pid, body.category, body.amount_nt, body.note))
    summary = budget.summarize(_used(pid), p["budget_cap"])
    if summary["level"] != "ok":
        await hub.emit(pid, {"type": "budget.threshold", **summary})
    return summary


# ── Job ─────────────────────────────────────────────────────────────────

class JobIn(BaseModel):
    kind: str
    payload: dict = {}


@app.post("/api/projects/{pid}/jobs", status_code=201)
def create_job(pid: str, body: JobIn):
    _load_project(pid)
    try:
        return jobs.create_job(pid, body.kind, body.payload)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/projects/{pid}/jobs")
def list_jobs(pid: str):
    _load_project(pid)
    return jobs.list_jobs(pid)


@app.websocket("/ws/projects/{pid}")
async def ws_project(ws: WebSocket, pid: str):
    await ws.accept()
    hub.clients[pid].add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.clients[pid].discard(ws)


# ── 前端（正式環境由 API 直接提供 frontend/dist；開發時用 Vite :5180）──────────

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        file = (DIST / path).resolve()
        if path and file.is_file() and file.is_relative_to(DIST):
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
