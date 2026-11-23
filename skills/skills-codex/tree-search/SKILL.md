---
name: tree-search
description: Explore measurable research candidates in separate Git worktrees with Draft, Improve, and Debug. Keep a bounded solution tree and score every attempt with eval-contract.
---

# Bounded research tree

Use the topic's real baseline probe and reviewed `SPEC.md`. The `kill-argument` SPEC review must PASS under the actual cross-family reviewer route. Freeze scoring with [eval-contract](../eval-contract/SKILL.md) before candidate coding. Resolve `ARIS_TOOLS` as described there.

Run the controller on macOS or Linux. Use a single controller and keep the evaluator, wiki, state, and evidence in controller custody. Keep a separate held-out split and an overall attempt and compute budget. Worktrees isolate Git edits; use a separate execution identity when candidate code can write controller files.

```sh
python3 "$ARIS_TOOLS/tree_search.py" init "$ROUND" --id "$ROUND_ID" --repo "$REPO" \
  --wiki "$WIKI" --probe "$PROBE_JSON" --spec "$SPEC" --review "$KILL_ARGUMENT_JSON" \
  --evaluator "$EVALUATOR" --expect-sha256 "$PIN" --budget "$N_ATTEMPTS"
```

Allocate **3–5 distinct technical routes** from the same root with `draft "$ROUND" --route ... --hypothesis ... --change ...`. A new route changes the mechanism, representation, or algorithm family. Reserve slots for exploratory Drafts before spending the budget on improvements. Extend the strongest scored node using `improve "$ROUND" --hypothesis ... --change ...`. Use `debug "$ROUND" --parent NODE --hypothesis ... --change ...` for an observed failure with a plausible focused repair. Earlier branches remain addressable by node and commit. All three operators consume one attempt.

Use the returned worktree for candidate code. Independent runs can execute in parallel within the user-approved compute budget. Commit each change, then pass the log and data-only output to the controller using `score "$ROUND" NODE --artifact ... --log ...`. Use `fail "$ROUND" NODE --log ...` for executions without a prediction artifact. Indicate `--cause-status confirmed` only with supporting evidence. Keep logs and evaluation artifacts outside candidate worktrees. Result recording invokes the frozen scorer and the same Research Wiki ledger used by eval-contract.

`status "$ROUND"` shows parent, branch, score, status and original budget. `resume "$ROUND" NODE` recovers an interrupted worktree allocation without using another slot. For an interrupted score handoff, repeat the original submission with the same files and annotations. Use `close "$ROUND"` once every started attempt has a record, then invoke [dream-consolidate](../dream-consolidate/SKILL.md). Before running held-out confirmation, lock the chosen candidate by commit using development scores.

Start a fresh round for the next cycle. To use a seed, add `--seed OLD_ROUND/SEED_ID` when drafting and review its hypothesis, cheapest test and falsifier first. The seed enters the trial as a hypothesis; its parent branches carry their own recorded scores.
