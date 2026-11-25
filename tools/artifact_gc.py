#!/usr/bin/env python3
"""Keep ARIS run artifacts under a disk cap.

Only regenerable directories are touched (default: .aris/cache, .aris/traces,
.aris/snapshots). Anything whose path is cited by a run state (.aris/runs) or
by the Research Wiki (research-wiki/) is kept, so evidence behind a conclusion
is never deleted. Dry run by default; pass --apply to delete.

Order: first everything older than --max-age-days, then oldest-first until the
total is under --cap-gb.
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

DEFAULT_TARGETS = (".aris/cache", ".aris/traces", ".aris/snapshots")
CITATION_SOURCES = (".aris/runs", "research-wiki")


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def _cited_text(root: Path) -> str:
    parts = []
    for src in CITATION_SOURCES:
        d = root / src
        for f in d.rglob("*") if d.is_dir() else []:
            if f.is_file() and f.suffix in {".json", ".jsonl", ".md", ".yaml", ".yml"}:
                parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def plan(root: Path, targets=DEFAULT_TARGETS, cap_gb: float = 2.0,
         max_age_days: float = 14.0, now: float | None = None) -> dict:
    """Return {'delete': [...], 'kept_cited': [...], 'total': bytes, 'after': bytes}."""
    now = time.time() if now is None else now
    cited = _cited_text(root)
    items, kept = [], []
    for t in targets:
        d = root / t
        for child in sorted(d.iterdir()) if d.is_dir() else []:
            if child.is_symlink():
                continue
            rel = child.relative_to(root).as_posix()
            if rel in cited or child.name in cited:
                kept.append(rel)
                continue
            items.append((child.stat().st_mtime, _size(child), child, rel))
    total = sum(s for _, s, _, _ in items) + sum(_size(root / k) for k in kept)
    cap = int(cap_gb * 1024 ** 3)
    delete, after = [], total
    for mtime, size, path, rel in sorted(items):  # oldest first
        if now - mtime > max_age_days * 86400 or after > cap:
            delete.append({"path": rel, "bytes": size})
            after -= size
    return {"delete": delete, "kept_cited": kept, "total": total, "after": after}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--cap-gb", type=float, default=2.0)
    ap.add_argument("--max-age-days", type=float, default=14.0)
    ap.add_argument("--target", action="append", help="override target dirs (repeatable)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    for t in a.target or []:
        if Path(t).is_absolute() or ".." in Path(t).parts:
            print(f"error: target must be relative inside root: {t}"); return 1
    p = plan(root, tuple(a.target or DEFAULT_TARGETS), a.cap_gb, a.max_age_days)
    mb = lambda b: f"{b / 1024 ** 2:.0f} MB"  # noqa: E731
    for d in p["delete"]:
        print(f"{'delete' if a.apply else 'would delete'}  {d['path']}  ({mb(d['bytes'])})")
        if a.apply:
            target = root / d["path"]
            shutil.rmtree(target) if target.is_dir() else target.unlink()
    print(f"kept (cited as evidence): {len(p['kept_cited'])}")
    print(f"{mb(p['total'])} -> {mb(p['after'])}" + ("" if a.apply else "  (dry run; add --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
