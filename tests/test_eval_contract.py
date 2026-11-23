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


def success(result):
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result.stdout.strip()


@pytest.fixture
def setup(tmp_path):
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    (evaluator / "evaluation.json").write_text(json.dumps({
        "entrypoint": "eval.py",
        "protocol": "Fixed development split fixture and test interpreter",
        "baseline": {"accuracy": 0.6},
        "primary_metric": "accuracy",
        "direction": "maximize",
        "minimum_improvement": 0.2,
        "timeout_seconds": 5,
    }))
    (evaluator / "labels.json").write_text('[1, 1, 0, 1, 0]')
    (evaluator / "eval.py").write_text('''import json, sys
from pathlib import Path
labels = json.loads(Path("labels.json").read_text())
predictions = json.loads(Path(sys.argv[1]).read_text())
assert len(predictions) == len(labels)
print(json.dumps({"accuracy": sum(x == y for x, y in zip(labels, predictions)) / len(labels)}))
''')
    artifact = tmp_path / "predictions.json"
    artifact.write_text('[1, 1, 0, 0, 0]')
    return evaluator, artifact, tmp_path / "receipt.json"


def test_scores_at_the_exact_frozen_threshold(setup):
    evaluator, artifact, receipt = setup
    pin = success(invoke("eval_contract", "freeze", evaluator))
    assert success(invoke("eval_contract", "check", evaluator, "--expect-sha256", pin)) == pin
    command = ("score", evaluator, "--expect-sha256", pin, "--artifact", artifact, "--receipt", receipt)
    success(invoke("eval_contract", *command))
    scored = json.loads(receipt.read_text())
    assert scored["status"] == "pass"
    assert scored["delta"] == {"accuracy": 0.2}
    assert invoke("eval_contract", *command).returncode != 0
    assert json.loads(receipt.read_text()) == scored


def test_minimization_uses_the_frozen_direction(setup):
    evaluator, artifact, receipt = setup
    config = evaluator / "evaluation.json"
    spec = json.loads(config.read_text())
    spec.update(direction="minimize", baseline={"accuracy": 0.9}, minimum_improvement=0.1)
    config.write_text(json.dumps(spec))
    pin = success(invoke("eval_contract", "freeze", evaluator))
    success(invoke("eval_contract", "score", evaluator, "--expect-sha256", pin,
                   "--artifact", artifact, "--receipt", receipt))
    score = json.loads(receipt.read_text())
    assert score["status"] == "pass"
    assert score["delta"] == {"accuracy": -0.1}


@pytest.mark.parametrize("filename", ["eval.py", "labels.json", "evaluation.json"])
def test_changed_evaluation_rejected_before_scoring(setup, filename):
    evaluator, artifact, receipt = setup
    pin = success(invoke("eval_contract", "freeze", evaluator))
    target = evaluator / filename
    target.write_text(target.read_text() + "\n")
    result = invoke("eval_contract", "score", evaluator, "--expect-sha256", pin,
                    "--artifact", artifact, "--receipt", receipt)
    assert "independently recorded SHA-256" in result.stderr
    assert result.returncode != 0 and not receipt.exists()


def test_symlinked_evaluator_dependency_is_rejected(setup):
    evaluator, artifact, _ = setup
    (evaluator / "outside.json").symlink_to(artifact)
    result = invoke("eval_contract", "freeze", evaluator)
    assert result.returncode != 0
    assert "regular files" in result.stderr


@pytest.mark.parametrize("output", ['{"accuracy":NaN}', '{"accuracy":true}',
                                     '{"accuracy":0,"accuracy":1}', '{"other":0.5}'])
def test_invalid_metric_output_remains_unscored(setup, output):
    evaluator, artifact, receipt = setup
    (evaluator / "eval.py").write_text(f"print({output!r})")
    pin = success(invoke("eval_contract", "freeze", evaluator))
    result = invoke("eval_contract", "score", evaluator, "--expect-sha256", pin,
                    "--artifact", artifact, "--receipt", receipt)
    evidence = json.loads(receipt.read_text())
    assert result.returncode != 0 and evidence["status"] == "error"
    assert "metrics" not in evidence


def test_ledger_carries_measured_failure_and_keeps_unscored_attempt(setup, tmp_path):
    evaluator, artifact, receipt = setup
    wiki = tmp_path / "research-wiki"
    success(invoke("research_wiki", "init", wiki))
    pin = success(invoke("eval_contract", "freeze", evaluator))
    artifact.write_text('[0, 0, 0, 0, 0]')
    success(invoke("eval_contract", "score", evaluator, "--expect-sha256", pin,
                   "--artifact", artifact, "--receipt", receipt))
    fields = ("--hypothesis", "Constant prediction improves accuracy", "--change", "Predict zeros",
              "--scope", "Fixed five-row development split", "--cause", "Class imbalance",
              "--cause-status", "tentative", "--revision", "candidate-v1")
    command = ("record_trial", wiki, "--id", "constant-zero", "--contract-sha256", pin,
               "--receipt", receipt, *fields)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda _: invoke("research_wiki", *command), range(2)):
            assert "TRIAL_RECORD_OK" in success(result)
    record = wiki / "trials" / "constant-zero.json"
    original = record.read_bytes()
    trial = json.loads(original)
    assert trial["outcome"] == "below_threshold" and trial["delta"] == {"accuracy": -0.2}
    assert trial["evidence_sha256"] and trial["cause_status"] == "tentative"
    changed = list(command)
    changed[changed.index("--change") + 1] = "Rewrite experiment history"
    assert invoke("research_wiki", *changed).returncode != 0
    assert record.read_bytes() == original
    other_pin = "0" * 64
    result = invoke("research_wiki", "record_trial", wiki, "--id", "wrong-pin",
                    "--contract-sha256", other_pin, "--receipt", receipt, *fields)
    assert result.returncode != 0
    log = tmp_path / "crash.log"
    log.write_text("Run aborted before prediction")
    success(invoke("research_wiki", "record_trial", wiki, "--id", "crashed", "--contract-sha256",
                   pin, "--failed-log", log, *fields))
    crash = json.loads((wiki / "trials" / "crashed.json").read_text())
    assert crash["outcome"] == "unscored" and crash["delta"] is None
    pack = (wiki / "query_pack.md").read_text()
    assert "constant-zero" in pack and "Fixed five-row development split" in pack
    assert "crashed" not in pack and "Run aborted" not in pack
