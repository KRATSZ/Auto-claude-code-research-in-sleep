"""tools/artifact_gc.py: cap, age, cited-evidence protection, dry run."""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import artifact_gc as gc  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "tools" / "artifact_gc.py"


def _blob(path: Path, size: int, age_days: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


def test_old_items_deleted_new_kept(tmp_path):
    _blob(tmp_path / ".aris/cache/old.bin", 10, 30)
    _blob(tmp_path / ".aris/cache/new.bin", 10, 1)
    p = gc.plan(tmp_path)
    assert [d["path"] for d in p["delete"]] == [".aris/cache/old.bin"]


def test_cap_deletes_oldest_first(tmp_path):
    for i, age in enumerate((3, 2, 1)):
        _blob(tmp_path / f".aris/traces/t{i}.log", 1024 ** 2, age)
    p = gc.plan(tmp_path, cap_gb=1.5 / 1024)  # 1.5 MB cap, 3 MB present
    assert [d["path"] for d in p["delete"]] == [".aris/traces/t0.log", ".aris/traces/t1.log"]


def test_cited_evidence_is_never_deleted(tmp_path):
    _blob(tmp_path / ".aris/cache/receipt-run7", 10, 99)
    (tmp_path / "research-wiki/trials").mkdir(parents=True)
    (tmp_path / "research-wiki/trials/t1.md").write_text("artifact: .aris/cache/receipt-run7")
    p = gc.plan(tmp_path, cap_gb=0)
    assert p["delete"] == [] and p["kept_cited"] == [".aris/cache/receipt-run7"]


def test_untargeted_dirs_untouched(tmp_path):
    _blob(tmp_path / ".aris/runs/r1.json", 10, 99)
    _blob(tmp_path / "experiments/x.bin", 10, 99)
    assert gc.plan(tmp_path, cap_gb=0)["delete"] == []


def test_cli_dry_run_then_apply(tmp_path):
    f = tmp_path / ".aris/cache/old"
    _blob(f, 10, 30)
    out = subprocess.run([sys.executable, str(TOOL), str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout
    assert "would delete" in out and f.exists()
    subprocess.run([sys.executable, str(TOOL), str(tmp_path), "--apply"], check=True,
                   capture_output=True)
    assert not f.exists()


def test_cli_rejects_escaping_target(tmp_path):
    r = subprocess.run([sys.executable, str(TOOL), str(tmp_path), "--target", "../x"],
                       capture_output=True, text=True)
    assert r.returncode == 1
