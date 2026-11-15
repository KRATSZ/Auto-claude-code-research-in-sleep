---
name: aris-research
description: "Route general research work through ARIS in Codex. Use for literature review, idea discovery, experiment planning, result interpretation, research review, or a full research lifecycle."
---

# ARIS research router

## Workflow checkpoints

Apply these at the point of action, including when a downstream skill advances automatically. Use the active topic's directory for both files. A focused literature search can end with its findings.

### 1. Run real baselines before proposing a method

Before brainstorming, proposing or implementing a new method, or drafting a paper, complete four sections in the topic's `RESEARCH_BRIEF.md`:

1. **Known problem.** Give its established research or engineering name.
2. **Strong baselines.** Choose the 2–3 strongest applicable methods and give each one's rationale, source, version, and runnable command.
3. **Real results.** Freeze success criteria. Run each existing implementation on the project's real data or observed failures with aligned inputs, metrics, and relevant budget. Record numbers and raw output paths. Reuse matching prior runs.
4. **Remaining gap.** State what remains unsolved and which cases show it. Recommend retiring the question only when real runs meet all success criteria. Reuse the existing implementation when a proposal duplicates prior work.

Run baseline-only checks and their setup during this stage. Before `idea-discovery` or `idea-creator` Phase 2, method design, new-method experiments, or paper drafting, require completed real runs and a demonstrated gap. Missing data, synthetic-only evidence, and baselines that cannot run leave the brief incomplete. Record what is missing and stay at the baseline stage.

### 2. Read and update `progress.md` every round

Seed the first card from baseline results before the first new-method experiment. Read the latest card before starting or resuming each round. Run its single change and comparison. In `experiment-bridge`, write after Phase 5 collects results. In `auto-review-loop`, read before Phase C and write in Phase E. Apply the same order to direct runs. For each round, including failed and final rounds, append four fields:

1. **Established.** Numbers and raw output paths, compared with the same strong baselines on the same real data and scale. Label incomplete comparisons.
2. **Unresolved or unexpected.** Record what is unmeasured or contradicted. Investigate contradictions first.
3. **Closed routes.** Record failed or duplicate approaches, with evidence and scope. Skip retries under the same conditions.
4. **Next round.** Name one change and its control, or the reason the experiment is finished. Before adding an unplanned idea, spend two minutes checking prior work and record the decision.

### 3. Try to disprove a breakthrough idea first

When the strongest baselines share a substantial failure on real cases and the user wants a fundamental advance, route to `research-refine` before any broad idea generation. Between Steps 1.2 and 1.3, record in the brief:

1. The shared failure and 3–5 assumptions the baselines have in common.
2. What happens when each assumption is reversed, and which reversals violate a physical or mathematical constraint.
3. Three approaches from other fields that address the same underlying problem, with sources and how each would transfer.
4. The most counterintuitive viable candidate and its cheapest decisive real test, with a strong baseline and a preset outcome that would rule it out.

Run that test as the next experiment round. Expand only if the test meets its preset criteria on real cases.

## Skill routes

Choose the narrowest useful route and follow its installed Codex skill:

| Request | Skill |
|---|---|
| Full lifecycle from a broad question to a paper-ready result | `research-pipeline` |
| Literature search or related work | `research-lit` |
| Explore and validate research ideas | `idea-discovery` |
| Check whether an idea is novel | `novelty-check` |
| Design an experiment plan | `experiment-plan` |
| Implement or execute an existing plan | `experiment-bridge` |
| Judge what results support | `result-to-claim` |
| Iteratively review and improve research | `auto-review-loop` |
| Maintain a persistent field/research map | `research-wiki` |
| Propose improvements to ARIS from accumulated logs | `meta-optimize` |
| Land a specifically user-approved proposal | `meta-apply` |

Do not expand a focused request into the full pipeline. Start or resume the ARIS usage log, record the selected skill before work, record stage outcomes and any explicit user correction, then close the workflow. Never include task text in the log. Require five completed workflow runs before proposing harness changes; `meta-optimize` is proposal-only, and landing remains a separate user decision.

The Codex mirror sometimes describes nested skills with Claude-style `/skill` notation. Interpret those as routes to the named installed Codex skill, not as shell commands. Preserve that skill's workflow and evidence gates while translating its handoffs to Codex.
