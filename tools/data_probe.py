#!/usr/bin/env python3
"""Run existing baselines on pinned local inputs and capture evidence for ideation."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from eval_contract import file_digest, parse_json, valid_metrics, write_once_json
from research_wiki import rebuild_index
from threat_scan import scan_for_threats


def read_json(path):
    return parse_json(Path(path).read_bytes())


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", value):
        raise ValueError("IDs must be at most 24 lowercase letters, digits, or hyphens")
    return value


def required_text(value):
    if not isinstance(value, str) or not value.strip() or scan_for_threats(value, scope="strict"):
        raise ValueError("expected nonempty, safe text")
    return value.strip()


def nonempty_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError(f"expected a nonempty regular file: {path}")
    return path


def verify_probe(path):
    path = nonempty_file(path)
    report = read_json(path)
    if report.get("schema") != 1 or report.get("usable") is not True or not report.get("inputs"):
        raise ValueError("a usable real-baseline probe is required")
    if any(file_digest(nonempty_file(name)) != digest for name, digest in report["inputs"].items()):
        raise ValueError("probe inputs have changed")
    for baseline in report["baselines"].values():
        for run in baseline["runs"]:
            for key in ("stdout", "stderr"):
                label = run[key]
                if Path(label).name != label or file_digest(path.parent / label) != run[f"{key}_sha256"]:
                    raise ValueError("probe output has changed")
    return report


def run_probe(plan_path: Path, wiki: Path, probe_id: str, cwd: Path) -> Path:
    identifier(probe_id)
    if not (wiki / "experiments").is_dir():
        raise ValueError("initialize the topic's research wiki first")
    expected_plan = file_digest(nonempty_file(plan_path))
    plan = read_json(plan_path)
    scope = required_text(plan["scope"])
    inputs = plan["inputs"]
    baselines = plan["baselines"]
    if not isinstance(inputs, list) or not inputs or not isinstance(baselines, list) or not baselines:
        raise ValueError("the probe requires real input files and at least one baseline")
    paths = [nonempty_file(cwd / Path(name)).resolve() for name in inputs]
    sources = {str(path): file_digest(path) for path in paths}
    specs = {}
    for spec in baselines:
        key = identifier(spec["id"])
        args = spec["command"]
        repeats = spec.get("repeats", 1)
        timeout = spec.get("timeout_seconds", 60)
        if (key in specs or not isinstance(args, list) or not args or
                not all(isinstance(arg, str) and arg for arg in args) or
                type(repeats) is not int or not 1 <= repeats <= 20 or
                type(timeout) is not int or timeout <= 0):
            raise ValueError("invalid baseline ID, argv, repeat count, or timeout")
        specs[key] = (args, repeats, timeout)

    run = wiki / "probes" / probe_id
    run.mkdir(parents=True)
    attempts = {}
    for key, (args, repeats, timeout) in specs.items():
        output = []
        for number in range(1, repeats + 1):
            log = run / f"{key}-{number}.stderr.log"
            raw = run / f"{key}-{number}.stdout.json"
            started = perf_counter()
            row = {"stdout": raw.name, "stderr": log.name}
            with raw.open("xb") as capture, log.open("xb") as errors:
                try:
                    with subprocess.Popen(args, cwd=cwd, stdout=capture, stderr=errors,
                                          start_new_session=os.name == "posix") as process:
                        try:
                            code = process.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            if os.name == "posix":
                                try:
                                    os.killpg(process.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            else:
                                process.kill()
                            process.wait()
                            raise
                    row.update(exit_code=code, status="ok" if code == 0 else "failed")
                except subprocess.TimeoutExpired:
                    row.update(status="timeout", exit_code=None)
                except OSError as exc:
                    errors.write(str(exc).encode("utf-8", errors="replace"))
                    row.update(status="failed", exit_code=None)
            row["elapsed_seconds"] = perf_counter() - started
            if row["status"] == "ok":
                try:
                    if raw.stat().st_size > 1024 * 1024:
                        raise ValueError("baseline JSON exceeds one MiB")
                    observed = read_json(raw)
                    metrics = observed["metrics"]
                    bad_cases = observed.get("bad_cases", [])
                    if (not valid_metrics(metrics) or not isinstance(bad_cases, list) or
                            scan_for_threats(json.dumps(observed, ensure_ascii=False, allow_nan=False),
                                             scope="strict")):
                        raise ValueError("invalid or unsafe probe output")
                    row.update(metrics=metrics, bad_cases=bad_cases)
                except (ValueError, KeyError, TypeError, AttributeError):
                    row["status"] = "invalid_output"
            row.update(stdout_sha256=file_digest(raw), stderr_sha256=file_digest(log))
            output.append(row)
        durations = sorted(row["elapsed_seconds"] for row in output)
        attempts[key] = {"runs": output, "p50_seconds": statistics.median(durations),
                         "p95_seconds": durations[(95 * len(durations) + 99) // 100 - 1]}

    try:
        unchanged = (all(file_digest(Path(path)) == digest for path, digest in sources.items()) and
                     file_digest(plan_path) == expected_plan)
    except OSError:
        unchanged = False
    usable = unchanged and any(row["status"] == "ok" for item in attempts.values() for row in item["runs"])
    report = run / "probe.json"
    write_once_json(report, dict(schema=1, scope=scope, inputs=sources, plan_sha256=expected_plan,
                                 stable_inputs=unchanged, usable=usable, baselines=attempts))
    rebuild_index(str(wiki))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--wiki", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.verify:
            verify_probe(args.verify)
            print(args.verify)
            return 0
        if not all((args.plan, args.wiki, args.id)):
            parser.error("capture requires --plan, --wiki and --id")
        report = run_probe(args.plan, args.wiki, args.id, args.cwd)
        print(report)
        return 0 if read_json(report)["usable"] else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"PROBE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
