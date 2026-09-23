"""門檻狀態機（UI.md §2.3）。

規則：
- 門檻依序通過；前面的門檻沒通過，後面的不能 approve。
- reject = 要求重做該門檻的產出，狀態回到 pending。
- reopen（回頭）一個已通過的門檻，會讓它之後所有已通過的門檻失效。
"""

from dataclasses import dataclass
from enum import StrEnum


class GateStatus(StrEnum):
    PENDING = "pending"  # 產出還沒好，或被退回重做
    READY = "ready"  # 產出好了，等使用者看／聽
    APPROVED = "approved"


@dataclass(frozen=True)
class Gate:
    id: str
    step: int
    name: str
    rework_cost: str  # 回頭的代價（白話，顯示在 UI）


GATES: tuple[Gate, ...] = (
    Gate("G1", 1, "分鏡表", "極低"),
    Gate("G2a", 2, "美術風格", "高：要重訓風格模型，之後所有鏡頭重做"),
    Gate("G2b", 2, "角色視覺", "高：要重訓角色模型"),
    Gate("G2c", 2, "聲線錨定", "中：所有對白要重念"),
    Gate("G3", 3, "場景與世界觀", "中：該場景的鏡頭要重做"),
    Gate("G4", 4, "配音與時間軸", "中：時間軸重鎖，3D 預視要重排"),
    Gate("G5", 5, "3D 運鏡預視", "低：每鏡 NT$0.23"),
    Gate("G6", 6, "草稿初篩", "中：每鏡 NT$1–7"),
    Gate("G7", 6, "終片驗收", "—"),
)
GATE_IDS = tuple(g.id for g in GATES)
GATE_BY_ID = {g.id: g for g in GATES}


class GateError(ValueError):
    pass


def initial_state() -> dict[str, GateStatus]:
    return {gid: GateStatus.PENDING for gid in GATE_IDS}


def _index(gid: str) -> int:
    if gid not in GATE_BY_ID:
        raise GateError(f"未知的門檻 {gid}")
    return GATE_IDS.index(gid)


def mark_ready(state: dict[str, GateStatus], gid: str) -> None:
    _index(gid)
    if state[gid] == GateStatus.APPROVED:
        raise GateError(f"{gid} 已通過，要先 reopen")
    state[gid] = GateStatus.READY


def approve(state: dict[str, GateStatus], gid: str) -> None:
    i = _index(gid)
    blocked = [g for g in GATE_IDS[:i] if state[g] != GateStatus.APPROVED]
    if blocked:
        raise GateError(f"要先通過 {', '.join(blocked)}")
    if state[gid] != GateStatus.READY:
        raise GateError(f"{gid} 的產出還沒準備好（{state[gid]}）")
    state[gid] = GateStatus.APPROVED


def reject(state: dict[str, GateStatus], gid: str) -> None:
    _index(gid)
    if state[gid] != GateStatus.READY:
        raise GateError(f"{gid} 目前沒有可退回的產出（{state[gid]}）")
    state[gid] = GateStatus.PENDING


def reopen(state: dict[str, GateStatus], gid: str) -> list[str]:
    """回頭修改已通過的門檻，回傳因此失效的後續門檻。"""
    i = _index(gid)
    if state[gid] != GateStatus.APPROVED:
        raise GateError(f"{gid} 尚未通過，不需要 reopen")
    state[gid] = GateStatus.READY
    invalidated = [g for g in GATE_IDS[i + 1 :] if state[g] == GateStatus.APPROVED]
    for g in GATE_IDS[i + 1 :]:
        state[g] = GateStatus.PENDING
    return invalidated


def current_step(state: dict[str, GateStatus]) -> int:
    """第一個尚未通過的門檻所在的步驟；全部通過則為 6。"""
    for g in GATES:
        if state[g.id] != GateStatus.APPROVED:
            return g.step
    return GATES[-1].step
