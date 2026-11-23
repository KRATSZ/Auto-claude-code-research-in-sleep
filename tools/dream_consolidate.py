#!/usr/bin/env python3
"""Prepare offline round evidence and commit scoped lessons and untested seeds."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from data_probe import identifier, nonempty_file, read_json, required_text
from eval_contract import file_digest, write_once_json
from research_wiki import rebuild_index, rebuild_query_pack
from threat_scan import quarantine
from tree_search import git, lock, TERMINAL


EXCERPT = 12_000


def excerpt(text, label):
    if len(text) > EXCERPT:
        text = text[:EXCERPT // 2] + "\n[… excerpt …]\n" + text[-EXCERPT // 2:]
    return quarantine(text, scope="strict", label=label)[0]


def packet(state):
    tree = read_json(state / "tree.json")
    if not tree["closed"] or any(node["status"] not in TERMINAL for node in tree["nodes"].values()):
        raise ValueError("close the tree round after all attempts have been recorded")
    nodes = []
    for node in tree["nodes"].values():
        trial = nonempty_file(node["trial"])
        log = nonempty_file(node["log"])
        if file_digest(trial) != node["trial_sha256"] or file_digest(log) != node["log_sha256"]:
            raise ValueError(f"round evidence changed for {node['node_id']}")
        record = read_json(trial)
        evidence = nonempty_file(Path(tree["wiki"]) / record["evidence"])
        if file_digest(evidence) != record["evidence_sha256"]:
            raise ValueError(f"trial receipt changed for {node['node_id']}")
        with log.open("rb") as stream:
            if log.stat().st_size > EXCERPT:
                head = stream.read(EXCERPT // 2)
                stream.seek(-EXCERPT // 2, os.SEEK_END)
                raw_log = head + b"\n[log excerpt]\n" + stream.read()
            else:
                raw_log = stream.read()
        delta = git(tree["repo"], "diff", "--no-ext-diff", "--no-textconv",
                    node["parent_revision"], node["revision"], "--")
        nodes.append({**node, "record": record,
                      "log_excerpt": excerpt(raw_log.decode("utf-8", errors="replace"), "run log"),
                      "diff_excerpt": excerpt(delta, "git diff")})
    return tree, dict(schema=1, round_id=tree["round_id"], contract_sha256=tree["contract_sha256"],
                      tree_sha256=file_digest(state / "tree.json"), nodes=nodes)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def validate_entries(entries, nodes, *, seeds):
    if not isinstance(entries, list):
        raise ValueError("lessons and seeds must be lists")
    seen = set()
    accepted = []
    for item in entries:
        key = identifier(item["id"])
        sources = item["sources"]
        if (key in seen or not isinstance(sources, list) or len(sources) < 2 or
                len(set(sources)) != len(sources) or (seeds and len(sources) != 2) or
                len({nodes[key]["route"] for key in sources}) < 2):
            raise ValueError("entries must cite distinct attempts from at least two technical routes")
        names = ("mechanism_a", "mechanism_b", "why_distant", "hypothesis", "test", "falsifier") if seeds else (
                 "observation", "scope", "counterevidence", "confidence")
        fields = {name: required_text(item[name]) for name in names}
        if not seeds and fields["confidence"] not in ("tentative", "supported"):
            raise ValueError("lesson confidence must be tentative or supported")
        seen.add(key)
        accepted.append(dict(id=key, sources=sources, **fields))
    return accepted


def write_pool(path, text):
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        pending = Path(staging) / path.name
        pending.write_text(text, encoding="utf-8")
        try:
            os.link(pending, path)
        except FileExistsError:
            if nonempty_file(path).read_bytes() != pending.read_bytes():
                raise ValueError("this round already has a different dream pool") from None


def consolidate(tree, bundle, notes_path):
    notes = read_json(nonempty_file(notes_path))
    anchor = digest(bundle)
    if notes.get("packet_sha256") != anchor:
        raise ValueError("consolidation does not match the current evidence packet")
    nodes = {node["node_id"]: node for node in bundle["nodes"]}
    lessons = validate_entries(notes["lessons"], nodes, seeds=False)
    seeds = validate_entries(notes["seeds"], nodes, seeds=True)
    wiki = Path(tree["wiki"])
    cycle = tree["round_id"]
    folder = wiki / "dreams" / cycle
    write_once_json(folder / "round.json", dict(schema=1, round_id=cycle, packet_sha256=anchor,
                    contract_sha256=tree["contract_sha256"], lessons=lessons, seeds=seeds))
    lines = [f"# Dream pool · {cycle}", "", "Untested hypotheses for future Draft attempts.", ""]
    for seed in seeds:
        lines.extend([f"## {cycle}/{seed['id']}", "", seed["hypothesis"], "",
                      f"- Parent attempts: {', '.join(seed['sources'])}",
                      f"- Mechanism A: {seed['mechanism_a']}", f"- Mechanism B: {seed['mechanism_b']}",
                      f"- Distance: {seed['why_distant']}", f"- Cheapest test: {seed['test']}",
                      f"- Falsifier: {seed['falsifier']}", ""])
    write_pool(folder / "dream_pool.md", "\n".join(lines) + "\n")
    with redirect_stdout(sys.stderr):
        rebuild_index(str(wiki))
        rebuild_query_pack(str(wiki))
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("packet")
    collect.add_argument("state", type=Path)
    collect.add_argument("--out", required=True, type=Path)
    commit = sub.add_parser("consolidate")
    commit.add_argument("state", type=Path)
    commit.add_argument("--notes", required=True, type=Path)
    args = parser.parse_args()
    try:
        with lock(args.state):
            tree, bundle = packet(args.state)
            if args.command == "packet":
                anchor = digest(bundle)
                write_once_json(args.out, dict(packet_sha256=anchor, **bundle))
                print(anchor)
            else:
                print(consolidate(tree, bundle, args.notes))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"DREAM_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
