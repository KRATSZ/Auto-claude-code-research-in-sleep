import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"


def call(tool, *args, cwd=None, expected=0):
    result = subprocess.run([sys.executable, str(TOOLS / f"{tool}.py"), *map(str, args)], cwd=cwd,
                            capture_output=True, text=True)
    assert result.returncode == expected, (result.stdout, result.stderr)
    return result.stdout.strip()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data():
    archive = os.environ.get("ARIS_REAL_IRIS_ARCHIVE")
    if not archive:
        training = [([1, 1, 1, 1], "a"), ([5, 5, 5, 5], "b"), ([9, 9, 9, 9], "c")]
        return training, training * 3
    with ZipFile(archive) as bundle:
        rows = [row for row in csv.reader(bundle.read("iris.data").decode().splitlines()) if row]
    groups = {label: [(list(map(float, row[:-1])), label) for row in rows if row[-1] == label]
              for label in sorted({row[-1] for row in rows})}
    return ([pair for rows in groups.values() for pair in rows[:35]],
            [pair for rows in groups.values() for pair in rows[35:45]])


@pytest.mark.skipif(os.name != "posix", reason="worktree controller uses POSIX file locks")
def test_real_baseline_tree_and_next_round_seed(tmp_path):
    repo = tmp_path / "candidate"
    wiki = tmp_path / "research-wiki"
    evaluator = tmp_path / "scorer"
    repo.mkdir()
    evaluator.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "ARIS Test")
    git(repo, "config", "user.email", "aris@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    call("research_wiki", "init", wiki)
    training, development = data()
    inputs = tmp_path / "worker-input.json"
    dump(inputs, {"training": training, "queries": [x for x, _ in development]})
    labels = [y for _, y in development]
    dump(evaluator / "labels.json", labels)
    (repo / "fit.py").write_text('''import json, sys
from pathlib import Path
from statistics import mean

data = json.loads(Path(sys.argv[1]).read_text())
mode = Path(__file__).with_name('config.txt').read_text().strip()
groups = {y: [x for x, yy in data['training'] if y == yy] for _, y in data['training']}
centers = {y: tuple(map(mean, zip(*rows))) for y, rows in groups.items()}
axes = {'raw': range(4), 'sepal': range(2), 'petal': range(2, 4)}
if mode == 'constant':
    predictions = [min(groups)] * len(data['queries'])
else:
    predictions = [min(centers, key=lambda y: sum((x[i] - centers[y][i]) ** 2 for i in axes[mode]))
                   for x in data['queries']]
Path(sys.argv[2]).write_text(json.dumps(predictions))
print('Measured', len(predictions), 'queries using', mode)
''', encoding="utf-8")
    (repo / "config.txt").write_text("raw", encoding="utf-8")
    git(repo, "add", "fit.py", "config.txt")
    git(repo, "commit", "-qm", "Existing nearest centroid baseline")
    baseline = tmp_path / "baseline.py"
    baseline.write_text('''import json, subprocess, sys
from pathlib import Path

subprocess.run([sys.executable, sys.argv[1], sys.argv[2], 'baseline-predictions.json'],
               check=True, stdout=sys.stderr)
truth = json.loads(Path(sys.argv[3]).read_text())
pred = json.loads(Path('baseline-predictions.json').read_text())
failures = [{'row': i, 'expected': y, 'observed': p} for i, (y, p) in enumerate(zip(truth, pred)) if y != p]
print(json.dumps({'metrics': {'accuracy': (len(truth)-len(failures))/len(truth)}, 'bad_cases': failures}))
''', encoding="utf-8")
    baseline_plan = tmp_path / "probe-plan.json"
    dump(baseline_plan, {"scope": "Iris development split" if len(labels) == 30 else "labeled fixture split",
                         "inputs": [str(inputs), str(evaluator / "labels.json")],
                         "baselines": [{"id": "centroid", "command": [sys.executable, str(baseline),
                             str(repo / "fit.py"), str(inputs), str(evaluator / "labels.json")], "repeats": 2}]})
    probe = Path(call("data_probe", "--plan", baseline_plan, "--wiki", wiki, "--id", "raw-baseline", "--cwd", tmp_path))
    measured = load(probe)
    samples = measured["baselines"]["centroid"]["runs"]
    assert measured["usable"] and len(samples) == 2
    if len(labels) == 30:
        assert samples[0]["metrics"] == {"accuracy": 28 / 30}
        assert len(samples[0]["bad_cases"]) == 2
    (evaluator / "eval.py").write_text('''import json, sys
from pathlib import Path
y = json.loads(Path('labels.json').read_text())
p = json.loads(Path(sys.argv[1]).read_text())
assert len(y) == len(p)
print(json.dumps({'accuracy': sum(a == b for a, b in zip(y, p))/len(y)}))
''', encoding="utf-8")
    dump(evaluator / "evaluation.json", {"entrypoint": "eval.py", "protocol": "fixed development split",
        "baseline": samples[0]["metrics"], "primary_metric": "accuracy", "direction": "maximize",
        "minimum_improvement": 0.01, "timeout_seconds": 5})
    pin = call("eval_contract", "freeze", evaluator)
    spec = tmp_path / "SPEC.md"
    spec.write_text("Compare model routes on the frozen development set.", encoding="utf-8")
    review = tmp_path / "KILL_ARGUMENT.json"
    # Review transport fixture. This test measures a real baseline when an archive is supplied;
    # it does not claim that any model was contacted or that a research idea passed review.
    dump(review, {"audit_skill": "kill-argument", "verdict": "PASS", "review_independence": "cross-family",
                  "acceptance_status": "accepted", "executor_model": "codex-gpt-6-astra", "executor_family": "openai",
                  "reviewer_model": "claude-opus-5-5", "reviewer_family": "anthropic", "trace_path": "fixture",
                  "audited_input_hashes": {"SPEC.md": f"sha256:{sha(spec)}"}})
    init_args = ("--repo", repo, "--wiki", wiki, "--probe", probe, "--spec", spec, "--review", review,
                 "--evaluator", evaluator, "--expect-sha256", pin, "--budget", 6)
    state = tmp_path / "round"
    call("tree_search", "init", state, "--id", "round-one", *init_args)

    def allocate(i):
        return json.loads(call("tree_search", "draft", state, "--route", f"route-{i}",
                               "--hypothesis", f"Route {i} can improve the baseline", "--change", f"Change {i}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        drafts = list(pool.map(allocate, range(4)))
    assert len({node["node_id"] for node in drafts}) == 4
    assert len({node["worktree"] for node in drafts}) == 4
    assert all(node["parent_id"] == "root" for node in drafts)

    def fit(node, mode, *, status=0):
        path = Path(node["worktree"])
        (path / "config.txt").write_text(mode, encoding="utf-8")
        git(path, "add", "config.txt")
        git(path, "commit", "--allow-empty", "-qm", node["node_id"])
        output = tmp_path / (node["node_id"] + ".json")
        log = tmp_path / (node["node_id"] + ".log")
        with log.open("w") as handle:
            fit = subprocess.run([sys.executable, path / "fit.py", inputs, output], cwd=path,
                                 stdout=handle, stderr=subprocess.STDOUT)
        assert fit.returncode == status
        return node, output, log

    with ThreadPoolExecutor(max_workers=4) as pool:
        fits = list(pool.map(lambda pair: fit(pair[0], pair[1], status=pair[2]),
                            zip(drafts, ("constant", "raw", "sepal", "invalid"), (0, 0, 0, 1))))
    scored = []
    for node, artifact, log in fits:
        if artifact.exists():
            scored.append(json.loads(call("tree_search", "score", state, node["node_id"],
                                         "--artifact", artifact, "--log", log)))
        else:
            failed = json.loads(call("tree_search", "fail", state, node["node_id"], "--log", log))
    assert failed["status"] == "error" and failed["score"] is None
    improved = json.loads(call("tree_search", "improve", state, "--hypothesis", "Change one distance rule",
                               "--change", "Try petal-only distance"))
    before = load(state / "tree.json")
    contenders = [n for n in before["nodes"].values() if n["status"] == "scored"]
    assert improved["parent_id"] == max(contenders, key=lambda row: row["score"])["node_id"]
    debug = json.loads(call("tree_search", "debug", state, "--parent", failed["node_id"],
                            "--hypothesis", "Fix the invalid configuration", "--change", "Restore a valid distance rule"))
    assert debug["parent_id"] == failed["node_id"]
    for item in (fit(improved, "petal"), fit(debug, "sepal")):
        node, artifact, log = item
        call("tree_search", "score", state, node["node_id"], "--artifact", artifact, "--log", log)
    call("tree_search", "draft", state, "--route", "overflow", "--hypothesis", "More", "--change", "More", expected=2)
    call("tree_search", "close", state)
    packet = tmp_path / "packet.json"
    packet_pin = call("dream_consolidate", "packet", state, "--out", packet)
    evidence = load(packet)
    assert len(evidence["nodes"]) == 6 and any("KeyError" in row["log_excerpt"] for row in evidence["nodes"])
    assert all(row["contract_sha256"] == pin for row in (n["record"] for n in evidence["nodes"]))
    sources = [scored[0]["node_id"], failed["node_id"]]
    notes = tmp_path / "notes.json"
    report = {"packet_sha256": packet_pin, "lessons": [{"id": "control", "sources": sources,
        "observation": "Both cited approaches missed the baseline target, with different evidence",
        "scope": "This round", "counterevidence": "One attempt was unscored", "confidence": "tentative"}],
        "seeds": [{"id": "recombine", "sources": sources, "mechanism_a": "Constant rule as a fallback",
                   "mechanism_b": "Distance decision on confident cases", "why_distant": "Fixed versus distance rule",
                   "hypothesis": "A fallback can handle ties in distance", "test": "Compare tie cases under the same split",
                   "falsifier": "Fallback adds errors to the tie cases"}]}
    dump(notes, {**report, "packet_sha256": "0" * 64})
    call("dream_consolidate", "consolidate", state, "--notes", notes, expected=2)
    dump(notes, report)
    pool = Path(call("dream_consolidate", "consolidate", state, "--notes", notes))
    assert "round-one/recombine" in (pool / "dream_pool.md").read_text()
    assert "Both cited approaches" in (wiki / "query_pack.md").read_text()
    assert len(list((wiki / "trials").glob("*.json"))) == 6
    next_round = tmp_path / "next-round"
    call("tree_search", "init", next_round, "--id", "round-two", *init_args)
    next_node = json.loads(call("tree_search", "draft", next_round, "--route", "hybrid",
                               "--change", "Add a fallback for ties", "--seed", "round-one/recombine"))
    assert next_node["hypothesis"] == report["seeds"][0]["hypothesis"]
    spec.write_text("Changed after review", encoding="utf-8")
    call("tree_search", "init", tmp_path / "stale", "--id", "round-three", *init_args, expected=2)
