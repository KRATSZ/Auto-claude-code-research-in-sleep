---
name: data-probe
description: Run existing baselines on real data before selecting research ideas. Capture metrics, elapsed-time distributions, errors, and concrete bad cases in the topic's Research Wiki.
---

# Real baseline probe

Use before `idea-discovery` when the research cycle is enabled. Reuse established baselines and commands. Read the project's research brief, choose comparable inputs, and state the concrete decision the probe could change. Baseline tuning or candidate generation belongs after this probe.

Initialize the topic's Research Wiki via `research_wiki.py init`. Resolve `ARIS_TOOLS` as described in [eval-contract](../eval-contract/SKILL.md). Write a baseline plan like this, with paths relative to the baseline working directory or absolute paths:

```json
{
  "scope": "Development split and the target runtime or quality problem",
  "inputs": ["data/development.csv"],
  "baselines": [
    {"id": "existing", "command": ["python3", "baseline.py"], "repeats": 3, "timeout_seconds": 60}
  ]
}
```

Include the files or pinned data manifest actually read by the baseline. If needed, add a thin wrapper around its normal entry point. The baseline emits one JSON object on stdout with finite numeric `metrics` and optional `bad_cases` carrying real case IDs and observed errors. Diagnostic noise goes to stderr. Use measured values from the baseline's normal output.

```sh
python3 "$ARIS_TOOLS/data_probe.py" --plan "$PLAN" --wiki "$WIKI" --id "$PROBE_ID" --cwd "$BASELINE_CWD"
```

On resume, use `python3 "$ARIS_TOOLS/data_probe.py" --verify "$PROBE_JSON"` to check the recorded inputs and baseline outputs. Read `research-wiki/probes/<id>/probe.json` and the referenced raw logs. Summarize the observed bottlenecks, error scope, and how each observation changes the shortlist in `RESEARCH_BRIEF.md`. A usable probe supplies real measurements. The brief also needs a demonstrated gap before proceeding to literature-anchored discovery. Preserve failed runs and investigate their evidence before proposing a workaround.
