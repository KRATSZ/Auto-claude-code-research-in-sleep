"""Intake + Stage Gate (accept / retry / reject) in tools/run_state.py."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_state as rs  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "tools" / "run_state.py"


@pytest.fixture
def run(tmp_path):
    rs.start_run(str(tmp_path), "r1", ["idea", "experiment", "review"])
    return str(tmp_path)


def test_intake_reports_missing_and_ignores_empty(run):
    rs.record_intake(run, "r1", {"goal": "beat baseline", "budget": ""})
    assert "goal" not in rs.missing_intake(run, "r1")
    assert "budget" in rs.missing_intake(run, "r1")
    rs.record_intake(run, "r1", {f: "x" for f in rs.INTAKE_FIELDS})
    assert rs.missing_intake(run, "r1") == []


def test_intake_rejects_unknown_field(run):
    with pytest.raises(ValueError):
        rs.record_intake(run, "r1", {"secret_knob": 1})


def test_accept_does_not_acquit_phase(run):
    rs.set_status(run, "r1", "idea", "done")
    e = rs.stage_gate(run, "r1", "idea", "accept", "ranked ideas present")
    assert e["effective"] == "accept"
    assert rs.load_run(run, "r1")["phases"][0]["status"] == "done"


def test_retry_resets_phase_then_escalates_to_reject(run):
    rs.set_status(run, "r1", "experiment", "done")
    for i in (1, 2):
        e = rs.stage_gate(run, "r1", "experiment", "retry", "submission missing")
        assert e["effective"] == "retry" and e["retries"] == i
        assert rs.load_run(run, "r1")["phases"][1]["status"] == "pending"
    e = rs.stage_gate(run, "r1", "experiment", "retry", "submission missing")
    assert e["effective"] == "reject" and "retry limit 2" in e["reason"]
    state = rs.load_run(run, "r1")
    assert state["phases"][1]["status"] == "failed"
    assert [d["effective"] for d in state["decisions"]] == ["retry", "retry", "reject"]


def test_custom_retry_limit_and_reject(run):
    assert rs.stage_gate(run, "r1", "idea", "retry", "x", max_retries=0)["effective"] == "reject"
    assert rs.stage_gate(run, "r1", "review", "reject", "scorer changed")["effective"] == "reject"


def test_gate_validates_input(run):
    with pytest.raises(ValueError):
        rs.stage_gate(run, "r1", "idea", "maybe", "x")
    with pytest.raises(ValueError):
        rs.stage_gate(run, "r1", "idea", "accept", "  ")


def test_cli_gate_and_intake(run):
    out = subprocess.run([sys.executable, str(TOOL), "intake", run, "r1", "--goal", "g"],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("missing: budget")
    out = subprocess.run([sys.executable, str(TOOL), "gate", run, "r1", "idea", "retry",
                          "--reason", "eval script crashed"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "retry"
    assert json.loads(out.stderr)["reason"] == "eval script crashed"
