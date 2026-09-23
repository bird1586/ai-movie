"""Job 佇列（UI.md §5.2）。

APScheduler 每秒掃一次 QUEUED 的 job 交給 Executor。本輪只有 MockExecutor；
GPUtw 執行器要等客製化映像建好後才實作（見 docs/可行性評估.md §6）。
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import db

# 每種 job 依序經過的後端狀態
STAGES: dict[str, tuple[str, ...]] = {
    "tts": ("PREPARING", "TTS_SYNTHESIZING", "AUDIO_QC"),
    "block": ("PREPARING", "BLOCKING"),
    "draft": ("PREPARING", "DRAFTING", "QC_SCREENING"),
    "render": ("PREPARING", "REFINING", "LIPSYNCING", "INTERPOLATING", "UPSCALING", "MIXING"),
}
TERMINAL = {"DONE", "FAILED", "CANCELLED"}

Emit = Callable[[str, dict], Awaitable[None]]


class Executor(Protocol):
    async def run(self, job: dict, update: Callable[[str, float], Awaitable[None]]) -> dict:
        """執行 job，過程中呼叫 update(status, progress)，回傳 verdict。"""
        ...


class MockExecutor:
    """不花錢的假執行器：照 STAGES 走完並回傳通過的 verdict。"""

    def __init__(self, stage_seconds: float = 0.5):
        self.stage_seconds = stage_seconds

    async def run(self, job, update):
        stages = STAGES[job["kind"]]
        for i, stage in enumerate(stages):
            await update(stage, i / len(stages))
            await asyncio.sleep(self.stage_seconds)
        return {"stage": job["kind"], "pass": True, "mock": True, "plain_zh": "✅ 模擬執行完成"}


def create_job(project_id: str, kind: str, payload: dict | None = None) -> dict:
    if kind not in STAGES:
        raise ValueError(f"未知的 job 種類 {kind}")
    job_id = uuid.uuid4().hex[:12]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload) VALUES (?, ?, ?, 'QUEUED', ?)",
            (job_id, project_id, kind, json.dumps(payload or {}, ensure_ascii=False)),
        )
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return db.row_to_dict(row, ("payload", "verdict")) if row else None


def list_jobs(project_id: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at, rowid", (project_id,)
        ).fetchall()
    return [db.row_to_dict(r, ("payload", "verdict")) for r in rows]


def _set(job_id: str, status: str, progress: float, verdict: dict | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, progress = ?, verdict = COALESCE(?, verdict),"
            " updated_at = datetime('now') WHERE id = ?",
            (status, progress, json.dumps(verdict, ensure_ascii=False) if verdict else None, job_id),
        )


class JobRunner:
    def __init__(self, executor: Executor, emit: Emit, max_concurrent: int = 2):
        self.executor = executor
        self.emit = emit
        self.max_concurrent = max_concurrent
        self.running: dict[str, asyncio.Task] = {}
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self.scheduler.add_job(self.tick, "interval", seconds=1, max_instances=1, coalesce=True)
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        for task in self.running.values():
            task.cancel()

    async def tick(self) -> None:
        free = self.max_concurrent - len(self.running)
        if free <= 0:
            return
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY created_at, rowid LIMIT ?", (free,)
            ).fetchall()
        for row in rows:
            job = db.row_to_dict(row, ("payload",))
            _set(job["id"], "PREPARING", 0)
            self.running[job["id"]] = asyncio.create_task(self._run(job))

    async def _run(self, job: dict) -> None:
        async def update(status: str, progress: float) -> None:
            _set(job["id"], status, progress)
            await self.emit(job["project_id"], {"type": "job.progress", "job_id": job["id"],
                                                "status": status, "progress": progress})

        try:
            verdict = await self.executor.run(job, update)
            _set(job["id"], "DONE", 1.0, verdict)
            await self.emit(job["project_id"], {"type": "job.done", "job_id": job["id"], "verdict": verdict})
        except Exception as e:  # 技術性失敗：記錄原因，讓使用者可以重試
            _set(job["id"], "FAILED", 0, {"pass": False, "error": str(e)})
            await self.emit(job["project_id"], {"type": "job.failed", "job_id": job["id"], "error": str(e)})
        finally:
            self.running.pop(job["id"], None)
