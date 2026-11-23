#!/usr/bin/env python3
"""Pin a reviewed evaluator and score a candidate's data artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


SHA256 = re.compile(r"[0-9a-f]{64}")
SPEC_KEYS = {"entrypoint", "protocol", "baseline", "primary_metric", "direction",
             "minimum_improvement", "timeout_seconds"}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once_json(path: Path, value: dict) -> None:
    """Publish a complete record atomically without replacing any existing one."""
    data = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        pending = Path(staging) / "record.json"
        pending.write_text(data, encoding="utf-8")
        try:
            os.link(pending, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data.encode("utf-8"):
                raise ValueError(f"record already exists with different content: {path}") from None


def parse_json(data: str | bytes):
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    return json.loads(data, object_pairs_hook=unique_object)


def finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def valid_metrics(metrics) -> bool:
    return (isinstance(metrics, dict) and bool(metrics) and
            all(name.strip() and finite_number(value) for name, value in metrics.items()))


def evaluator_files(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"evaluator must be a regular directory: {root}")
    for path in sorted(root.iterdir()):
        if path.is_symlink():
            raise ValueError(f"evaluator must contain only regular files: {path}")
        if path.is_dir():
            yield from evaluator_files(path)
        elif path.is_file():
            yield path
        else:
            raise ValueError(f"evaluator must contain only regular files: {path}")


def fingerprint(root: Path, snapshot: Path | None = None) -> tuple[str, dict]:
    inventory = {}
    for path in evaluator_files(root):
        relative = path.relative_to(root)
        if snapshot is not None:
            frozen_file = snapshot / relative
            frozen_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, frozen_file)
            frozen_file.chmod(0o444)
            path = frozen_file
        inventory[relative.as_posix()] = file_digest(path)

    spec = parse_json(((snapshot or root) / "evaluation.json").read_bytes())
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        raise ValueError("evaluation.json requires exactly: " + ", ".join(sorted(SPEC_KEYS)))
    entrypoint = spec["entrypoint"]
    if not isinstance(entrypoint, str) or entrypoint not in inventory or not entrypoint.endswith(".py"):
        raise ValueError("entrypoint must be a Python script inside the evaluator directory")
    if not isinstance(spec["protocol"], str) or not spec["protocol"].strip():
        raise ValueError("protocol must describe the evaluation split and environment")
    metric = spec["primary_metric"]
    if not valid_metrics(spec["baseline"]) or not isinstance(metric, str) or metric not in spec["baseline"]:
        raise ValueError("baseline must contain finite numeric metrics including primary_metric")
    if spec["direction"] not in ("maximize", "minimize"):
        raise ValueError("direction must be maximize or minimize")
    if not finite_number(spec["minimum_improvement"]) or spec["minimum_improvement"] < 0:
        raise ValueError("minimum_improvement must be a finite nonnegative number")
    if not finite_number(spec["timeout_seconds"]) or spec["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    manifest = json.dumps({"schema": 1, "files": inventory}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(manifest).hexdigest(), spec


def require_digest(actual: str, expected: str) -> None:
    if not SHA256.fullmatch(expected) or actual != expected:
        raise ValueError("evaluator differs from the independently recorded SHA-256 pin")


def score(root: Path, expected: str, artifact: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError(f"receipt already exists: {output}; use a new attempt path")
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError("candidate artifact must be a regular file")
    if root.resolve() in output.resolve().parents:
        raise ValueError("store receipts outside the frozen evaluator directory")

    with tempfile.TemporaryDirectory(prefix="aris-eval-") as staging:
        snapshot = Path(staging) / "evaluator"
        snapshot.mkdir()
        digest, spec = fingerprint(root, snapshot)
        require_digest(digest, expected)
        candidate_input = Path(staging) / "artifact"
        shutil.copyfile(artifact, candidate_input)
        candidate_input.chmod(0o444)
        artifact_digest = file_digest(candidate_input)
        receipt = {
            "schema": 1,
            "contract_sha256": digest,
            "artifact_sha256": artifact_digest,
            "status": "error",
        }
        try:
            result = subprocess.run(
                [sys.executable, "-E", "-s", "-B", str(snapshot / spec["entrypoint"]),
                 str(candidate_input)],
                cwd=snapshot, capture_output=True, timeout=spec["timeout_seconds"], check=False,
            )
            if result.returncode:
                if result.stderr:
                    receipt["stderr_tail"] = result.stderr[-2000:].decode("utf-8", errors="replace")
                raise ValueError(f"evaluator exited with {result.returncode}")
            require_digest(fingerprint(snapshot)[0], expected)
            if file_digest(candidate_input) != artifact_digest:
                raise ValueError("evaluator modified the input artifact")
            metrics = parse_json(result.stdout)
            baseline = spec["baseline"]
            if not valid_metrics(metrics) or metrics.keys() != baseline.keys():
                raise ValueError("evaluator must emit exactly the frozen finite numeric metrics")
            deltas = {key: Decimal(str(metrics[key])) - Decimal(str(baseline[key])) for key in metrics}
            changes = {key: float(delta) for key, delta in deltas.items()}
            if not valid_metrics(changes):
                raise ValueError("metric change is outside the supported finite range")
            gain = deltas[spec["primary_metric"]]
            if spec["direction"] == "minimize":
                gain = -gain
            passed = gain >= Decimal(str(spec["minimum_improvement"]))
            receipt.update(status="pass" if passed else "below_threshold", metrics=metrics,
                           baseline=baseline, delta=changes, primary_metric=spec["primary_metric"])
        except subprocess.TimeoutExpired:
            receipt["error"] = "evaluator timed out"
        except (OSError, ValueError) as exc:
            receipt["error"] = str(exc)
    write_once_json(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "check", "score"):
        command = sub.add_parser(name)
        command.add_argument("evaluator", type=Path)
        if name != "freeze":
            command.add_argument("--expect-sha256", required=True)
        if name == "score":
            command.add_argument("--artifact", type=Path, required=True)
            command.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "score":
            result = score(args.evaluator, args.expect_sha256, args.artifact, args.receipt)
            print(result["status"], args.receipt)
            return 1 if result["status"] == "error" else 0
        digest, _ = fingerprint(args.evaluator)
        if args.command == "check":
            require_digest(digest, args.expect_sha256)
        print(digest)
        return 0
    except (OSError, ValueError) as exc:
        print(f"EVAL_CONTRACT_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
