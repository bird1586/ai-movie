import pytest

from app import gates
from app.gates import GateError, GateStatus as S


def test_must_approve_in_order():
    st = gates.initial_state()
    gates.mark_ready(st, "G2a")
    with pytest.raises(GateError, match="G1"):
        gates.approve(st, "G2a")


def test_cannot_approve_without_output():
    st = gates.initial_state()
    with pytest.raises(GateError, match="還沒準備好"):
        gates.approve(st, "G1")


def test_reject_returns_to_pending():
    st = gates.initial_state()
    gates.mark_ready(st, "G1")
    gates.reject(st, "G1")
    assert st["G1"] == S.PENDING


def test_reopen_invalidates_later_gates():
    st = gates.initial_state()
    for g in ("G1", "G2a", "G2b"):
        gates.mark_ready(st, g)
        gates.approve(st, g)
    assert gates.current_step(st) == 2
    invalidated = gates.reopen(st, "G1")
    assert invalidated == ["G2a", "G2b"]
    assert st["G1"] == S.READY and st["G2a"] == S.PENDING
    assert gates.current_step(st) == 1


def test_all_approved_is_step_6():
    st = {g: S.APPROVED for g in gates.GATE_IDS}
    assert gates.current_step(st) == 6
