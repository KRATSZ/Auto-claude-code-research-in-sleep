---
name: eval-contract
description: Freeze a reviewed evaluator, baseline, and metrics before candidate experiments, then score candidate artifacts and record every attempt in the Research Wiki.
---

# Frozen evaluation contract

Use after the candidate clears its adversarial review (for example, `kill-argument`) and before writing candidate code. For a scoped measurable experiment, add `EVALUATION_CONTRACT` to the experiment plan with the evaluator directory, independently recorded SHA-256, review reference, artifact format, and wiki path. This field activates the frozen-evaluation path on resume and in delegated runs.

1. Reproduce the baseline on the development split. Have the human or independent reviewer approve the evaluator, data and split, runtime, baseline numbers, primary metric, direction, and threshold. Put `evaluation.json`, reviewed Python scorer and its evaluation inputs in a dedicated evaluator directory controlled separately from the candidate workspace. Keep the approval and SHA-256 in the controlling plan outside the worker's writable checkout.
2. Use `freeze` once to compute the pin. Before implementing or resuming, use `check` with the recorded pin. Replay the baseline artifact through `score` and confirm zero metric deltas before testing candidates. If the evaluator needs a correction, freeze a new version under a new approval and consistently rescore the baseline and candidates. Retain the old receipts.
3. The worker emits a single **data** artifact, such as prediction JSON. The controller runs `score` in its approved environment. The scorer reads that artifact as data and prints one JSON object with exactly the frozen numeric metric keys. The CLI scores from a verified snapshot and writes a new receipt. `pass` and `below_threshold` are both successfully scored outcomes; `error` has no score. A pin mismatch stops before execution and creates no receipt. On resume, reuse a matching existing receipt after checking its pin and artifact digest.
4. Have the controller record every attempt using `research_wiki.py record_trial`. Use a fresh ID per attempt and a commit or diff digest for `--revision`. Supply the score receipt or, for an unscored execution, the failed-run log. Separate observed evidence from a proposed cause using `--cause-status`. Retain the raw artifact/evidence with its record. The query pack carries scoped measured failures; execution or setup errors remain in the trial ledger.

`evaluation.json` example:

```json
{
  "entrypoint": "eval.py",
  "protocol": "Development split v1; fixed seed 42; approved Python environment",
  "baseline": {"accuracy": 0.80},
  "primary_metric": "accuracy",
  "direction": "maximize",
  "minimum_improvement": 0.02,
  "timeout_seconds": 60
}
```

All regular files in the evaluator directory contribute to the pin. The entrypoint receives the artifact path as its only argument and runs in the evaluator directory. Keep imports and inputs within that snapshot or in the approved pinned runtime. Retain a separate held-out split for claims after adaptive search. Use a separate identity or runner if the worker could rewrite the pin, evaluator, scoring environment, or receipts. The helper checks integrity against that pin; filesystem permissions under one account do not provide isolation.

Resolve `ARIS_REPO` from the installed project manifest or use the ARIS checkout. A local checkout exposes `tools/`; an installed project may expose `.aris/tools/`. Resolve both helpers from the same tool directory.

```sh
if [ -z "${ARIS_REPO:-}" ] && [ -f .aris/installed-skills-codex.txt ]; then
  ARIS_REPO=$(awk -F '\t' '$1=="repo_root"{print $2; exit}' .aris/installed-skills-codex.txt) || true
fi
ARIS_TOOLS="${ARIS_REPO:-.}/tools"
[ -f "$ARIS_TOOLS/eval_contract.py" ] || ARIS_TOOLS=.aris/tools
python3 "$ARIS_TOOLS/eval_contract.py" freeze "$EVALUATOR"
# PIN comes from the independently recorded plan, including on resume.
python3 "$ARIS_TOOLS/eval_contract.py" check "$EVALUATOR" --expect-sha256 "$PIN"
python3 "$ARIS_TOOLS/eval_contract.py" score "$EVALUATOR" --expect-sha256 "$PIN" \
  --artifact "$PREDICTIONS" --receipt "$RECEIPT"
python3 "$ARIS_TOOLS/research_wiki.py" record_trial "$WIKI" --id "$TRIAL_ID" \
  --contract-sha256 "$PIN" --receipt "$RECEIPT" --revision "$REVISION" \
  --hypothesis "$HYPOTHESIS" --change "$CHANGE" --scope "$SCOPE" \
  --cause "$CAUSE" --cause-status "$CAUSE_STATUS"
```

Initialize the scoped wiki with `research_wiki.py init` if needed. Use `--failed-log "$RUN_LOG"` in place of `--receipt` for a run without a score receipt. Require `TRIAL_RECORD_OK` and the matching ledger file before handing off a completed attempt.
