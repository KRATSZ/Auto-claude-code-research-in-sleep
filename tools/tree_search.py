#!/usr/bin/env python3
"""Bounded research branches backed by Git worktrees and the frozen evaluator."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

from data_probe import identifier, nonempty_file, read_json, required_text, verify_probe
from eval_contract import file_digest, fingerprint, require_digest, score, write_once_json
from provenance import model_family
from research_wiki import record_trial


TERMINAL = {"scored", "error"}


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@contextmanager
def lock(state):
    if fcntl is None:
        raise ValueError("this controller requires POSIX file locking")
    with (state / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def save(state, tree):
    with tempfile.TemporaryDirectory(dir=state) as staging:
        pending = Path(staging) / "tree.json"
        pending.write_text(json.dumps(tree, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        pending.replace(state / "tree.json")


def copy_evidence(source, destination, expected):
    source = nonempty_file(source)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        pending = Path(staging) / "evidence"
        shutil.copyfile(source, pending)
        if file_digest(pending) != expected:
            raise ValueError(f"evidence changed during capture: {source}")
        try:
            os.link(pending, destination)
        except FileExistsError:
            if expected != file_digest(nonempty_file(destination)):
                raise ValueError(f"attempt already has different evidence: {destination}") from None


def init(args):
    identifier(args.id)
    if args.budget < 1:
        raise ValueError("the search budget must be positive")
    repo = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
    wiki = args.wiki.resolve()
    if not (wiki / "experiments").is_dir():
        raise ValueError("initialize the topic's research wiki first")
    probe_path = nonempty_file(args.probe).resolve()
    if wiki / "probes" not in probe_path.parents:
        raise ValueError("use a probe from this topic wiki")
    verify_probe(probe_path)
    spec_path = nonempty_file(args.spec).resolve()
    spec_digest = file_digest(spec_path)
    review_path = nonempty_file(args.review).resolve()
    review = read_json(review_path)
    author, reviewer = (model_family(review.get(key)) for key in ("executor_model", "reviewer_model"))
    if (review.get("audit_skill") != "kill-argument" or review.get("verdict") != "PASS" or
            review.get("review_independence") != "cross-family" or review.get("acceptance_status") != "accepted" or
            author == reviewer or {author, reviewer} & {"unknown", "deterministic"} or
            (author, reviewer) != (review.get("executor_family"), review.get("reviewer_family")) or
            not review.get("trace_path") or
            review.get("audited_input_hashes", {}).get(spec_path.name) != "sha256:" + spec_digest):
        raise ValueError("an accepted cross-family kill-argument PASS for this SPEC is required")
    evaluator = args.evaluator.resolve()
    digest, contract = fingerprint(evaluator)
    require_digest(digest, args.expect_sha256)
    tree = dict(schema=1, round_id=args.id, repo=str(repo), wiki=str(wiki),
                evidence={str(path): file_digest(path) for path in (probe_path, spec_path, review_path)},
                evaluator=str(evaluator), contract_sha256=digest, metric=contract["primary_metric"],
                direction=contract["direction"], budget=args.budget, closed=False, nodes={},
                root=dict(node_id="root", revision=git(repo, "rev-parse", "HEAD^{commit}")))
    args.state.mkdir(parents=True, exist_ok=True)
    with lock(args.state):
        path = args.state / "tree.json"
        if path.exists():
            existing = read_json(path)
            if any(existing[key] != value for key, value in tree.items() if key not in {"nodes", "closed"}):
                raise ValueError("this directory already contains a different round")
            return existing
        if any(entry.name != ".lock" for entry in args.state.iterdir()):
            raise ValueError("use an empty state directory")
        write_once_json(wiki / "search-runs" / f"{args.id}.json", {"tree": str(path)})
        save(args.state, tree)
    return tree


def ensure_worktree(tree, node):
    path = Path(node["worktree"])
    repo = tree["repo"]
    branch = node["branch_name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        branches = git(repo, "branch", "--list", branch, "--format=%(refname:short)")
        if branches:
            if git(repo, "rev-parse", f"refs/heads/{branch}") != node["parent_revision"]:
                raise ValueError("existing branch has different ancestry")
            git(repo, "worktree", "add", path, branch)
        else:
            git(repo, "worktree", "add", "-b", branch, path, node["parent_revision"])
    if (path.is_symlink() or Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path or
            git(path, "branch", "--show-current") != branch):
        raise ValueError("worktree does not match the allocated branch")
    common = lambda root: Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if common(path) != common(repo):
        raise ValueError("worktree is attached to a different repository")
    return path


def seed_for(tree, key):
    round_id, seed_id = key.split("/", 1)
    source = Path(tree["wiki"]) / "dreams" / identifier(round_id) / "round.json"
    records = read_json(nonempty_file(source))["seeds"]
    identifier(seed_id)
    found = next((seed for seed in records if seed["id"] == seed_id), None)
    if found is None:
        raise ValueError(f"seed not found: {key}")
    return found


def add_node(args, tree):
    nodes = tree["nodes"]
    if tree["closed"] or len(nodes) >= tree["budget"]:
        raise ValueError("the search round is closed or has used its attempt budget")
    if any(file_digest(Path(path)) != digest for path, digest in tree["evidence"].items()):
        raise ValueError("the probe, SPEC, or review changed")
    require_digest(fingerprint(Path(tree["evaluator"]))[0], tree["contract_sha256"])
    if args.operator == "draft":
        parent = tree["root"]
        if any(n["operator"] == "draft" and n["route"].casefold() == args.route.strip().casefold()
               for n in nodes.values()):
            raise ValueError("Draft needs a new technical route")
    elif args.operator == "improve":
        eligible = [n for n in nodes.values() if n["status"] == "scored"]
        if not eligible:
            raise ValueError("Improve needs a scored parent")
        rank = max if tree["direction"] == "maximize" else min
        parent = rank(eligible, key=lambda n: n["score"])
    else:
        parent = nodes[args.parent]
        if parent["status"] != "error":
            raise ValueError("Debug needs a failed parent")
    pending = [n for n in nodes.values() if n["status"] not in TERMINAL]
    if len(pending) >= 5:
        raise ValueError("complete an active branch before allocating a sixth worktree")

    node_id = f"n{len(nodes) + 1:03d}"
    route = required_text(args.route) if args.operator == "draft" else parent["route"]
    seed = seed_for(tree, args.seed) if args.operator == "draft" and args.seed else None
    hypothesis = required_text(args.hypothesis or (seed["hypothesis"] if seed else ""))
    node = dict(node_id=node_id, parent_id=parent["node_id"], parent_revision=parent["revision"],
                branch_name=f"aris/{tree['round_id']}/{node_id}", route=route,
                hypothesis=hypothesis, change=required_text(args.change),
                operator=args.operator, score=None, status="provisioning",
                worktree=str(args.state / "worktrees" / node_id))
    if args.operator == "draft" and args.seed:
        node["seed"] = args.seed
    nodes[node_id] = node
    save(args.state, tree)
    ensure_worktree(tree, node)
    node["status"] = "open"
    save(args.state, tree)
    return node


def finish(args, tree):
    node = tree["nodes"][args.node]
    if node["status"] != "open" or tree["closed"]:
        raise ValueError("attempt is not open")
    workspace = ensure_worktree(tree, node)
    if git(workspace, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("commit the candidate changes before recording the result")
    revision = git(workspace, "rev-parse", "HEAD")
    if git(workspace, "merge-base", node["parent_revision"], revision) != node["parent_revision"]:
        raise ValueError("candidate revision no longer descends from the recorded parent")
    if args.command == "score" and revision == node["parent_revision"]:
        raise ValueError("commit the candidate change before scoring")
    output = args.state / "attempts" / args.node
    output.mkdir(parents=True, exist_ok=True)
    definition = dict(revision=revision, kind=args.command, cause=required_text(args.cause),
                      cause_status=args.cause_status, log=file_digest(nonempty_file(args.log)))
    if args.command == "score":
        definition["artifact"] = file_digest(nonempty_file(args.artifact))
    write_once_json(output / "submission.json", definition)
    log = output / "run.log"
    copy_evidence(args.log, log, definition["log"])
    receipt = None
    value = None
    if args.command == "score":
        artifact = output / "predictions"
        copy_evidence(args.artifact, artifact, definition["artifact"])
        receipt = output / "score.json"
        if receipt.is_file():
            result = read_json(receipt)
            if (result["contract_sha256"] != tree["contract_sha256"] or
                    result["artifact_sha256"] != definition["artifact"]):
                raise ValueError("existing score receipt does not match the original submission")
        else:
            result = score(Path(tree["evaluator"]), tree["contract_sha256"], artifact, receipt)
        if result["status"] != "error":
            value = result["metrics"][tree["metric"]]
    with redirect_stdout(sys.stderr):
        trial = record_trial(Path(tree["wiki"]), f"{tree['round_id']}-{args.node}", tree["contract_sha256"],
                             hypothesis=node["hypothesis"], change=node["change"],
                             scope=f"Round {tree['round_id']}; {node['route']}", cause=definition["cause"],
                             cause_status=args.cause_status, revision=revision, receipt=receipt,
                             failed_log=None if receipt else log)
    node.update(status="error" if value is None else "scored", score=value, revision=revision,
                trial=str(trial), trial_sha256=file_digest(trial), log=str(log), log_sha256=definition["log"])
    save(args.state, tree)
    return node


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("init")
    create.add_argument("state", type=Path)
    for name in ("repo", "wiki", "probe", "spec", "review", "evaluator"):
        create.add_argument(f"--{name}", type=Path, required=True)
    create.add_argument("--id", required=True)
    create.add_argument("--expect-sha256", required=True)
    create.add_argument("--budget", type=int, required=True)
    for action in ("draft", "improve", "debug", "resume", "status", "score", "fail", "close"):
        command = sub.add_parser(action)
        command.add_argument("state", type=Path)
        if action in ("draft", "improve", "debug"):
            command.set_defaults(operator=action)
            command.add_argument("--hypothesis", required=action != "draft")
            command.add_argument("--change", required=True)
        if action == "draft":
            command.add_argument("--route", required=True)
            command.add_argument("--seed")
        if action == "debug":
            command.add_argument("--parent", required=True)
        if action in ("resume", "score", "fail"):
            command.add_argument("node")
        if action in ("score", "fail"):
            command.add_argument("--log", required=True, type=Path)
            command.add_argument("--cause", default="Unknown; further diagnostic evidence needed")
            command.add_argument("--cause-status", choices=("unknown", "tentative", "confirmed"), default="unknown")
        if action == "score":
            command.add_argument("--artifact", required=True, type=Path)
    args = parser.parse_args()
    args.state = args.state.resolve()
    try:
        if args.command == "init":
            result = init(args)
        else:
            with lock(args.state):
                tree = read_json(args.state / "tree.json")
                if args.command in ("draft", "improve", "debug"):
                    result = add_node(args, tree)
                elif args.command in ("score", "fail"):
                    result = finish(args, tree)
                elif args.command == "resume":
                    result = tree["nodes"][args.node]
                    if result["status"] == "provisioning":
                        ensure_worktree(tree, result)
                        result["status"] = "open"
                        save(args.state, tree)
                elif args.command == "close":
                    if not tree["nodes"] or any(n["status"] not in TERMINAL for n in tree["nodes"].values()):
                        raise ValueError("record every active attempt before closing the round")
                    tree["closed"] = True
                    save(args.state, tree)
                    result = tree
                else:
                    result = tree
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"TREE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
