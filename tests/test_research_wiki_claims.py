"""Tests for claim pages, graph wiring and derived status."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import research_wiki as rw  # noqa: E402


class TestClaimLayer(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        rw.init_wiki(self.root)

    def _page(self, slug):
        return Path(self.root) / "claims" / f"{slug}.md"

    def _edges(self):
        p = Path(self.root) / "graph" / "edges.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def test_creates_page_with_provenance_and_default_threshold(self):
        rw.add_claim(self.root, "thm-main-ub", "Main upper bound",
                     provenance=".aris/traces/proof-checker/r1",
                     statement="X <= Y")
        fm = self._page("thm-main-ub").read_text()
        self.assertIn("node_type: claim", fm)
        self.assertIn("node_id: claim:thm-main-ub", fm)
        self.assertIn("evidence_threshold: 2", fm)
        self.assertEqual(rw.claim_status(self.root, "thm-main-ub")["status"], "open")
        self.assertIn(".aris/traces/proof-checker/r1", fm)

    def test_slug_honored_verbatim(self):
        rw.add_claim(self.root, "b1-sieve-lb", "Sieve lower bound")
        self.assertTrue(self._page("b1-sieve-lb").is_file())

    def test_manually_specified_status_rejected(self):
        for status in ("supported", "refuted", "open", "verified", ""):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "derived"):
                rw.add_claim(self.root, "manual", "Manual", status=status)

    def test_legacy_status_is_ignored(self):
        page = rw.add_claim(self.root, "thm-gap", "Has an open gap")
        page.write_text(page.read_text().replace("node_type: claim", "node_type: claim\nstatus: verified"))
        self.assertEqual(rw.claim_status(self.root, "thm-gap")["status"], "open")

    def test_custom_threshold(self):
        rw.add_claim(self.root, "custom", "Claim", evidence_threshold=3)
        self.assertEqual(rw.claim_status(self.root, "custom")["evidence_threshold"], 3)

    def test_dedup_skips_without_update_flag(self):
        rw.add_claim(self.root, "thm-x", "X")
        rw.add_claim(self.root, "thm-x", "Y")
        self.assertIn('# X', self._page("thm-x").read_text())

    def test_update_on_exist_preserves_evidence_threshold(self):
        rw.add_claim(self.root, "thm-x", "X", evidence_threshold=3)
        rw.add_claim(self.root, "thm-x", "Y", update_on_exist=True)
        self.assertIn('# Y', self._page("thm-x").read_text())
        self.assertEqual(rw.claim_status(self.root, "thm-x")["evidence_threshold"], 3)

    def test_edges_wired(self):
        rw.add_claim(self.root, "thm-y", "Y",
                     uses=["paper:foo2024"], depends_on=["thm-x"], refutes=["thm-z"])
        kinds = {(e["from"], e["type"], e["to"]) for e in self._edges()}
        self.assertIn(("claim:thm-y", "uses", "paper:foo2024"), kinds)
        self.assertIn(("claim:thm-y", "depends_on", "claim:thm-x"), kinds)
        self.assertIn(("claim:thm-y", "refutes", "claim:thm-z"), kinds)

    def test_uninitialized_wiki_raises(self):
        bare = tempfile.mkdtemp()  # no init → no claims/ dir
        with self.assertRaises(RuntimeError):
            rw.add_claim(bare, "thm-x", "X")

    def test_appears_in_index(self):
        rw.add_claim(self.root, "thm-idx", "Indexed claim")
        idx = (Path(self.root) / "index.md")
        self.assertTrue(idx.is_file())
        self.assertIn("`claim:thm-idx` — Indexed claim [open]", idx.read_text())


if __name__ == "__main__":
    unittest.main()
