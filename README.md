# Code Review Benchmark

[English](README.md) · [Русский](README.ru.md)

> **One diff. Several models. A leaderboard for your codebase.**

Compare how different LLMs review *your* code. Take a real diff, run it
through multiple models, adjudicate the findings, and get per-model
**precision, recall, and hallucination rate** — on your code, not somebody
else's toy problems.

- **Use this if** you're choosing an LLM for CI review, a PR-commenting bot,
  or an IDE plugin with bounded context.
- **Don't use this if** you need agentic review with full repo navigation
  and tool use — that's a different benchmark (see [Scope](#scope-what-this-measures-and-what-it-doesnt) below).

Author: Svetlana Meleshkina. Licensed under [MIT](LICENSE).

## Why

Public benchmarks (SWE-bench, HumanEval, etc.) measure code generation.
Review is a different task and breaks differently: the model invents bugs
that aren't there, misses real ones, inflates severity. This harness
measures those failures on a diff you supply.

Two steps in the pipeline require reasoning — clustering the findings and
ruling on each cluster. You can do this **interactively with an agent**
(e.g. Kimi, Claude, Cursor) or **automate it with the LLM judge** via OpenRouter.
The parsing and the summing-up are done by code.

## Scope: what this measures (and what it doesn't)

This bench measures a model in **bounded-context single-shot review** mode:
a diff plus N context files in a single call through the OpenRouter API —
no tool use, no follow-ups, no access to the rest of the codebase. Same
input for every model, so results are comparable and runs reproducible.

**What this bench doesn't measure:**

- **agentic review** — the model navigates the repo, reads callsites,
  verifies hypotheses by running code. That's a `model + tools` pipeline,
  and the leaderboard there can look different;
- **harness effect** — Copilot+Opus, Claude Code+Opus and a bare API+Opus
  call give different results on the same prompt;
- **reasoning outside the context block** — if a bug is only provable
  via a file you didn't pass in, nobody will catch it.

**When this bench applies:** picking a model for CI review, a PR-commenting
bot, or an IDE plugin with bounded context.

**Agentic track:** for interactive review with tool use (model navigates the
repo, reads callsites), there is a separate harness —
`code_review_benchmark_agent.py`. It runs the same diff through an
OpenRouter + Serena MCP loop and produces output compatible with the same
metrics pipeline. See `docs/plans/2026-05-09-agentic-track.md` for details.
Requires Serena (see Install).

**Sensitivity.** Results depend on the quality of the code in the diff
(the gap between models on messy code is not the same as on clean code)
and on the prompt ("prove each finding" measurably cuts the hallucination
rate). Run on several diffs of varying difficulty, and keep the prompt
fixed when comparing models.

## Pipeline

```mermaid
flowchart TD
    Input([diff + context files])

    Input --> S1[1. code_review_benchmark.py<br/>OpenRouter, N models]
    S1 --> A1[results.json<br/>+ results/&lt;model&gt;.md]

    A1 --> S2[2. aggregate_findings.py parse]
    S2 --> A2[findings.json]

    A2 --> S3[3. Cluster — Your agent or llm_judge.py]
    A2 -. alt .-> S3alt[llm_judge.py cluster]
    S3 --> A3[clusters.json]
    S3alt -.-> A3

    A3 --> S4[4. aggregate_findings.py render]
    S4 --> A4[worklist.md]

    A4 --> S5[5. Adjudicate — Your agent or llm_judge.py]
    A4 -. alt .-> S5alt[llm_judge.py adjudicate]
    S5alt -.-> Draft[verdicts.draft.md]
    Draft -. human review .-> Human
    S5 --> Human{{verdicts.md<br/>human-approved}}

    Human --> S6[6. compute_metrics.py]
    S6 --> Out1[worklist_judged.md]
    S6 --> Out2[leaderboard.md]
    S6 -. --report .-> Out3[findings_report.md]

    classDef llm fill:#fef3c7,stroke:#d97706,color:#000
    classDef py fill:#dbeafe,stroke:#2563eb,color:#000
    classDef human fill:#fce7f3,stroke:#be185d,color:#000,font-weight:bold
    classDef artifact fill:#f3f4f6,stroke:#6b7280,color:#000

    class S1,S3,S3alt,S5,S5alt llm
    class S2,S4,S6 py
    class Human human
    class Input,A1,A2,A3,A4,Draft,Out1,Out2,Out3 artifact
```

Legend: yellow — LLM calls (OpenRouter or your agent), blue — Python
without LLM, pink — human checkpoint. Dashed = automated path via
`llm_judge.py`.

**Principle:** Python scripts (parsing, rendering, metrics) don't call any
LLM. For the reasoning steps (clustering, adjudication) you have two options:

- **Your agent** — interactive, usually free in chat mode (Claude, Kimi,
  Cursor, etc.).
- **LLM judge** (`llm_judge.py`) — automated via OpenRouter, reproducible,
  works with any model, no subscription needed.

Both paths produce the same artefacts (`clusters.json`, `verdicts.md`).
The automated judge emits a **draft** (`verdicts.draft.md`) that you review
before the metrics step. See both options below.

## Install

```bash
pip install -r requirements.txt
```

OpenRouter API key: <https://openrouter.ai/keys>

```powershell
$env:OPENROUTER_API_KEY = "..."   # PowerShell
```
```bash
export OPENROUTER_API_KEY=...     # bash
```

**Agentic track only** (optional): the agentic harness
(`code_review_benchmark_agent.py`) requires [Serena](https://github.com/oraios/serena)
as an external MCP server. Serena is not on PyPI yet; install it via `uvx`:

```bash
# Verify Serena is available
uvx --from git+https://github.com/oraios/serena serena --version
```

The bounded-context track (`code_review_benchmark.py`) does not need Serena.

## Quick start

```bash
# 1. Run models on your diff
python code_review_benchmark.py my.diff -c file.cs -o runs/demo/results.json

# 2. Parse findings
python aggregate_findings.py parse --results-dir runs/demo/results -o runs/demo/findings.json

# 3. Cluster (Your agent or llm_judge.py cluster)
# 4. Render worklist
python aggregate_findings.py render --findings runs/demo/findings.json \
  --clusters runs/demo/clusters.json -o runs/demo/worklist.md

# 5. Adjudicate (Your agent or llm_judge.py adjudicate)
# 6. Compute metrics
python compute_metrics.py --verdicts runs/demo/verdicts.md \
  --findings runs/demo/findings.json --clusters runs/demo/clusters.json \
  --results runs/demo/results.json --leaderboard runs/demo/leaderboard.md
```

Steps 3 and 5 require reasoning — see the detailed guide below.

## How to use

### 1. Run the models via OpenRouter

```bash
python code_review_benchmark.py path/to/some.diff \
  -c path/to/file1.cs \
  -c path/to/file2.cs \
  -o runs/<run-id>/results.json
```

Output:
- `results.json` — metadata + raw responses from every model
- `results/<model>.md` — one markdown file per model's review

**Models.** Default list — `models.json` (display name → OpenRouter model
id). Trim it to your quota or point to a different file with
`--models-file PATH`.

**Prompt.** Template at `prompts/review.en.txt`. There's also
`prompts/review.ru.txt` — Russian body with English field markers
(`Findings:`, `Location:`, …). Use your own with `--prompt PATH`.
Placeholders: `{diff}` and `{context_block}`.

### 2. Parse findings

```bash
python aggregate_findings.py parse \
  --results-dir results \
  -o findings.json
```

### 3. Clustering

Group findings by the underlying problem. Result — `clusters.json`:

```json
{
  "clusters": [
    {"id": 1, "topic": "...", "consensus_severity": "major", "members": [<int idx>]}
  ]
}
```

**Option A — Your agent (interactive, usually free in chat mode):**
Open the run folder in your agent and ask:
> Read `findings.json`. Group the findings by the same underlying problem
> (rubric — `prompts/cluster.en.txt`). Write the result to `clusters.json`.

**Option B — LLM judge (automated, via OpenRouter):**

```bash
python llm_judge.py cluster \
  --findings runs/<id>/findings.json \
  -o runs/<id>/clusters.json \
  --judge-model openai/gpt-5.5
```

### 4. Build the worklist

```bash
python aggregate_findings.py render \
  --findings findings.json \
  --clusters clusters.json \
  -o worklist.md
```

### 5. Adjudication

For each cluster, look at the source at the cited location and assign a
verdict: `real | smell | nit | wrong`. Result — `verdicts.md`:

```
## Cluster 1
- Verdict: real
- Confidence: high
- Reason: <one line>

## Cluster 2
...
```

**The human owns the final verdict.** Both paths below produce a draft.
Read it through and fix what needs fixing before computing metrics.

**Option A — Your agent (interactive, usually free in chat mode):**
> For each cluster in `worklist.md` read the source at `Location:` and
> assign a verdict using the rubric in `prompts/judge.en.txt`. Write to
> `verdicts.md`.

**Option B — LLM judge (automated, via OpenRouter):**
Produces `verdicts.draft.md` — rename to `verdicts.md` after review.

```bash
python llm_judge.py adjudicate \
  --clusters runs/<id>/clusters.json \
  --findings runs/<id>/findings.json \
  --repo-path /path/to/repo \
  --context-lines 50 \
  -o runs/<id>/verdicts.draft.md \
  --judge-model openai/gpt-5.5
```

At the top of the draft is a "Needs human attention" preamble listing
clusters worth a closer look: low-confidence calls, severity disagreement
between models, singletons (only one model found it).

### 6. Compute metrics

```bash
python compute_metrics.py \
  --verdicts verdicts.md \
  --findings findings.json \
  --clusters clusters.json \
  --results results.json
```

Output:
- `worklist_judged.md` — worklist with `[x]` filled in and judge notes
  inlined (handy for verification, especially low-confidence clusters)
- `leaderboard.md` — results table: precision, recall, hallucination rate,
  $/real per model

### 7. Narrative report (optional)

Add `--report` and the script generates a report with auto-filled tables
(real bugs, who-found-what, severity calibration, cost/value) plus
`<!-- TODO -->` blocks for your commentary. Template —
`templates/findings_report.template.md`.

```bash
python compute_metrics.py \
  --verdicts runs/<id>/verdicts.md \
  --findings runs/<id>/findings.json \
  --clusters runs/<id>/clusters.json \
  --results  runs/<id>/results.json \
  --leaderboard runs/<id>/leaderboard.md \
  --report      runs/<id>/findings_report.md
```

Then you fill in the prose in the `<!-- TODO -->` blocks — for an article
or an internal team writeup.

## LLM judge (automated path)

`llm_judge.py` runs clustering and adjudication through OpenRouter. Use it
when you want **reproducibility** (same prompt → same result), **multi-judge**
comparisons (run GPT-5.5, Kimi K2 and DeepSeek and compare agreement),
or when you prefer automation over chat.

```bash
# Clustering
python llm_judge.py cluster \
  --findings runs/<id>/findings.json \
  -o runs/<id>/clusters.json \
  --judge-model openai/gpt-5.5

# Adjudication — produces verdicts.draft.md, NOT verdicts.md
python llm_judge.py adjudicate \
  --clusters runs/<id>/clusters.json \
  --findings runs/<id>/findings.json \
  --repo-path /path/to/repo \
  --context-lines 50 \
  -o runs/<id>/verdicts.draft.md \
  --judge-model openai/gpt-5.5
```

Rubrics live in `prompts/cluster.en.txt` and `prompts/judge.en.txt`. Tune
them to your codebase before relying on the output.

**Things to remember:**
- LLM-as-judge has known biases (position, length, self-preference). If
  the judge is from the same family as a model under review, that model
  gets a small head start. Run multiple judges for reproducibility.
- The judge sees only ±N lines around `Location:`, not the whole file or
  its callsites. If the verdict depends on caller code, the judge will
  mark `Confidence: low` and you'll need to walk the call sites by hand.
- The "human owns the final call" principle still holds: the file is
  named `verdicts.draft.md`. You rename it to `verdicts.md` only after
  review.

## Verdict categories

- **real** — a genuine bug with production impact: crash, wrong result,
  degraded behaviour on typical inputs, race, leak, data loss
- **smell** — code health, won't crash: duplication, bad names, missing
  docs, DRY violations, asymmetric APIs
- **nit** — pure style: whitespace, micro-optimisation, idiomatic preference
- **wrong** — the model is mistaken: the issue doesn't exist, the code was
  misread, or the recommendation doesn't apply

Disputed cases: real/smell → smell, smell/nit → nit, smell/wrong →
re-check, otherwise smell.

## Format compatibility

Not every model follows the format perfectly. `aggregate_findings.py` has
an `ISSUE_RE` regex that's tolerant to the common deviations (`**bold**`,
`1.` instead of `1)`, severity wrapped in markdown, etc.) plus a
`FORMAT_NOTES = {}` dictionary for "parses but with caveats" annotations.
Your notes appear next to the model in `worklist.md` so reviewers give
those findings a bit of extra scrutiny. Fill it from your own runs;
mechanics — in
[CONTRIBUTING.md](CONTRIBUTING.md#format-compliance-notes).

Both parsers expect the English field markers from `prompts/review.en.txt`:
`Findings:`, `Location:`, `Why it matters:`, `Evidence:`, `Recommendation:`,
plus `[severity: blocker/major/minor/nit]`. Change the markers — update
the regexes.

## Repository layout

```
ai-code-review-benchmark/
├── README.md
├── README.ru.md
├── LICENSE
├── requirements.txt
├── code_review_benchmark.py        ← runner: bounded-context single-shot via OpenRouter
├── code_review_benchmark_agent.py  ← runner: agentic track via OpenRouter + Serena MCP
├── aggregate_findings.py           ← parses findings + builds worklist (no LLM)
├── compute_metrics.py              ← metrics + results table + report (no LLM)
├── llm_judge.py                    ← automated clustering and adjudication via OpenRouter
├── models.json                     ← default model list
├── prompts/
│   ├── review.en.txt               ← reviewer prompt (step 1)
│   ├── review.ru.txt               ← Russian body + English markers
│   ├── cluster.en.txt              ← clustering rubric (step 3)
│   └── judge.en.txt                ← adjudication rubric (step 5)
├── templates/
│   └── findings_report.template.md ← skeleton for --report
└── runs/                           ← .gitignored — local runs
    └── <run-id>/
        ├── input.diff
        ├── results.json
        ├── results/
        ├── findings.json
        ├── clusters.json
        ├── worklist.md
        ├── verdicts.draft.md
        ├── verdicts.md
        ├── worklist_judged.md
        ├── leaderboard.md
        └── findings_report.md
```

**Run id:** `runs/<short-id>/` — a ticket (`PROJ-1234`), a feature
(`auth-refactor`) or a date (`2026-05-09-deepseek-only`). All artefacts of
one run live in one folder.

`runs/` is in `.gitignore` so runs against private code don't leak into
the public repo. Drop the line only if the run is fully public.

## Run artefacts (`runs/<run-id>/`)

| File | What it is | Producer |
|---|---|---|
| `input.diff` | Unified diff | you (`git diff > input.diff`) |
| `results.json` | Metadata + raw model responses. Per-model `cost` and `reasoning_tokens` from OpenRouter (may be `null`). | `code_review_benchmark.py` |
| `results/<model>.md` | One model's review: `Findings:` with numbered items and sub-items `Location:`, `Why it matters:`, `Evidence:`, `Recommendation:` | `code_review_benchmark.py` |
| `findings.json` | `{issues: [{model, severity, summary, location, why_it_matters, evidence, recommendation}]}` | `aggregate_findings.py parse` |
| `clusters.json` | `{clusters: [{id, topic, consensus_severity, members: [int]}]}` | Your agent or `llm_judge.py cluster` |
| `worklist.md` | Clusters with `[ ]` checkboxes, ready for labelling | `aggregate_findings.py render` |
| `verdicts.draft.md` | Draft verdicts + "Needs human attention" preamble. After review → `verdicts.md` | `llm_judge.py adjudicate` |
| `verdicts.md` | Per-cluster verdicts (`## Cluster N`, `Verdict:`, `Confidence:`, `Reason:`) | Your agent or human review of the draft |
| `worklist_judged.md` | Worklist with `[x]` and judge notes | `compute_metrics.py` |
| `leaderboard.md` | Precision, recall, hallucination rate, $/real | `compute_metrics.py` |
| `findings_report.md` | Narrative report with tables and `<!-- TODO -->` blocks for prose | `compute_metrics.py --report` |
| `cost_estimates.json` | **Override only.** Cost is normally read from `results.json`. This file is for models routed outside OpenRouter, or when `usage.cost` is `null`. Keys are sanitised names: `re.sub(r"[^\w\-]+", "_", name)` | you (optional) |

## How to contribute

[CONTRIBUTING.md](CONTRIBUTING.md) — how to add a model, tweak the prompt,
or update the parser regexes.
