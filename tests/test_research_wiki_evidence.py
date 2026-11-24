import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"


def invoke(tool, *args):
    return subprocess.run([sys.executable, str(TOOLS / f"{tool}.py"), *map(str, args)],
                          capture_output=True, text=True)


def check(result, code=0, error=""):
    assert result.returncode == code, (result.stdout, result.stderr)
    assert error in result.stderr
    return result.stdout.strip()


def wiki_call(wiki, command, *args):
    return invoke("research_wiki", command, wiki, *args)


@pytest.fixture
def wiki(tmp_path):
    root = tmp_path / "wiki"
    check(wiki_call(root, "init"))
    return root


@pytest.fixture
def trial(wiki):
    evaluator = wiki.parent / "evaluator"
    evaluator.mkdir()
    (evaluator / "eval.py").write_text(
        "from pathlib import Path\nimport sys\nprint(Path(sys.argv[1]).read_text())\n",
        encoding="utf-8")
    (evaluator / "evaluation.json").write_text(json.dumps({
        "entrypoint": "eval.py", "protocol": "Fixed fixture metric",
        "baseline": {"accuracy": 0.4}, "primary_metric": "accuracy",
        "direction": "maximize", "minimum_improvement": 0.1, "timeout_seconds": 5,
    }), encoding="utf-8")
    pin = check(invoke("eval_contract", "freeze", evaluator))

    def record(trial_id, accuracy):
        artifact = wiki.parent / f"{trial_id}-predictions.json"
        artifact.write_text(json.dumps({"accuracy": accuracy}), encoding="utf-8")
        receipt = wiki.parent / f"{trial_id}-receipt.json"
        check(invoke("eval_contract", "score", evaluator, "--expect-sha256", pin,
                     "--artifact", artifact, "--receipt", receipt))
        check(wiki_call(wiki, "record_trial", "--id", trial_id, "--contract-sha256", pin,
                        "--hypothesis", "fixed threshold", "--change", "candidate predictions",
                        "--scope", "frozen fixture", "--cause", "unknown", "--cause-status",
                        "unknown", "--revision", "fixture", "--receipt", receipt))
        return receipt, hashlib.sha256(receipt.read_bytes()).hexdigest()

    return record


def create_claim(wiki, *args):
    return wiki_call(wiki, "add_claim", "--slug", "candidate", "--name", "Candidate", *args)


def edge(wiki, trial_id, digest, kind="supports", *args, source="exp:run"):
    return wiki_call(wiki, "add_edge", "--type", kind, "--from", source,
                     "--to", "claim:candidate", "--trial-id", trial_id,
                     "--receipt-sha256", digest, *args)


def state(wiki):
    return json.loads(check(wiki_call(wiki, "claim_status", "--slug", "candidate")))


@pytest.mark.parametrize("kind", ("supports", "invalidates"))
@pytest.mark.parametrize("pin", ((), ("--trial-id", "good"), ("--receipt-sha256", "a" * 64)))
def test_evidence_pin_is_mandatory(wiki, kind, pin):
    check(create_claim(wiki))
    graph = wiki / "graph" / "edges.jsonl"
    check(wiki_call(wiki, "add_edge", "--type", kind, "--from", "exp:run",
                    "--to", "claim:candidate", *pin), 2, "evidence edge requires")
    assert graph.read_bytes() == b""


def test_receipt_validation_rejects_wrong_hash_id_and_outcome(wiki, trial):
    check(create_claim(wiki))
    _, digest = trial("good", 0.6)
    check(edge(wiki, "good", "0" * 64), 2, "hash differs from trial")
    check(edge(wiki, "missing", digest), 2, "missing regular trial record")
    check(edge(wiki, "good", digest, source="trial:other"), 2, "does not match trial")
    check(edge(wiki, "good", digest, "invalidates"), 2, "cannot justify invalidates")
    assert state(wiki)["status"] == "open"


def test_mixed_verdicts_duplicate_receipts_and_configurable_settlement(wiki, trial):
    check(create_claim(wiki, "--evidence-threshold", 2))
    check(wiki_call(wiki, "add_edge", "--type", "uses", "--from", "claim:candidate",
                    "--to", "paper:existing"))
    _, first = trial("first", 0.5)
    _, duplicate = trial("duplicate", 0.5)
    _, negative = trial("negative", 0.3)
    _, second = trial("second", 0.6)
    _, third = trial("third", 0.7)
    assert first == duplicate
    check(edge(wiki, "first", first))
    check(edge(wiki, "duplicate", duplicate))
    assert state(wiki) == dict(claim_id="claim:candidate", status="supported", supporting=1,
                               refuting=0, settled=False, evidence_threshold=2)
    check(edge(wiki, "negative", negative, "invalidates"))
    assert state(wiki)["status"] == "open"
    check(edge(wiki, "second", second))
    assert not state(wiki)["settled"]
    check(edge(wiki, "third", third))
    assert state(wiki)["status"] == "supported" and state(wiki)["settled"]
    graph = wiki / "graph" / "edges.jsonl"
    before = graph.read_bytes()
    assert "Edge already exists" in check(edge(wiki, "third", third))
    check(wiki_call(wiki, "add_edge", "--type", "depends_on", "--from", "claim:other",
                    "--to", "claim:candidate"), 2, "settled")
    check(wiki_call(wiki, "add_edge", "--type", "uses", "--from", "claim:candidate",
                    "--to", "paper:new"), 2, "settled")
    check(create_claim(wiki, "--update-on-exist", "--uses", "paper:new"), 2, "settled")
    check(create_claim(wiki, "--update-on-exist", "--evidence-threshold", 9), 2, "fixed")
    assert graph.read_bytes() == before
    assert "Candidate [supported, settled]" in (wiki / "index.md").read_text()


def test_read_and_write_fail_closed_if_a_pinned_receipt_changes(wiki, trial):
    check(create_claim(wiki, "--evidence-threshold", 1))
    receipt, digest = trial("negative", 0.3)
    check(edge(wiki, "negative", digest, "invalidates"))
    assert state(wiki)["status"] == "refuted" and state(wiki)["settled"]
    receipt.write_bytes(receipt.read_bytes() + b"\n")
    check(wiki_call(wiki, "claim_status", "--slug", "candidate"), 2, "content hash differs")
    check(wiki_call(wiki, "add_edge", "--type", "uses", "--from", "claim:candidate",
                    "--to", "paper:existing"), 2, "content hash differs")
    check(wiki_call(wiki, "rebuild_query_pack"))
    assert "invalidates [unverified]" in (wiki / "query_pack.md").read_text()


def test_manual_status_rejected_by_cli(wiki):
    check(create_claim(wiki, "--status", "supported"), 2, "unrecognized arguments")
    assert not list((wiki / "claims").glob("*.md"))


def test_concurrent_extensions_cannot_cross_a_settled_claim(wiki, trial):
    check(create_claim(wiki, "--evidence-threshold", 1))
    _, first = trial("first", 0.6)
    _, second = trial("second", 0.7)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda row: edge(wiki, *row),
                                [("first", first), ("second", second)]))
    assert sorted(result.returncode for result in results) == [0, 2]
    assert "settled" in next(result.stderr for result in results if result.returncode)
    assert state(wiki)["supporting"] == 1


def test_migration_is_lossless_and_idempotent(wiki, trial):
    check(create_claim(wiki))
    graph = wiki / "graph" / "edges.jsonl"
    legacy = {"from": "exp:run", "to": "claim:candidate", "type": "supports",
              "evidence": "<metric>", "added": "2026-11-23T00:00:00Z"}
    untouched = b' { "from":"paper:a","to":"paper:b","type":"extends" }\r\nunknown-line\n'
    original = untouched + json.dumps(legacy).encode() + b"\r\n"
    graph.write_bytes(original)
    assert "Would mark 1" in check(wiki_call(wiki, "migrate_evidence", "--dry-run"))
    assert graph.read_bytes() == original
    assert state(wiki)["status"] == "open"
    assert "Marked 1" in check(wiki_call(wiki, "migrate_evidence"))
    migrated = graph.read_bytes()
    assert migrated.startswith(untouched) and migrated.endswith(b"\r\n")
    assert json.loads(migrated.splitlines()[-1]) == dict(legacy, legacy=True)
    _, digest = trial("good", 0.6)
    check(edge(wiki, "good", digest))
    with_pin = graph.read_bytes()
    assert "Marked 0" in check(wiki_call(wiki, "migrate_evidence"))
    assert graph.read_bytes() == with_pin
    assert state(wiki)["supporting"] == 1
