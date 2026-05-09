# Code Review Benchmark

[English](README.md) · [Русский](README.ru.md)

A methodology and tooling for benchmarking LLM code-review quality on a real
diff: run the same diff through N models, aggregate findings, deduplicate,
adjudicate, and compute per-model precision / recall / hallucination rate.

Author: Svetlana Meleshkina. Licensed under [MIT](LICENSE).

## Why

Public LLM-coding benchmarks (SWE-bench, HumanEval, etc.) measure code
*generation*, not code *review*. Review quality has different failure modes:
hallucinated bugs, missed real bugs, severity inflation, format
non-compliance. This repo is a small, opinionated harness for measuring those
on your own diffs — so you can pick a model for *your* codebase, not a
leaderboard's.

The methodology deliberately keeps a human-in-the-loop for two judgement
steps (clustering and per-cluster adjudication). LLMs do the bulk parsing
work; a person (helped by Claude in chat) makes the calls that matter.

## Pipeline

```
[diff + context files]
        ↓
1. code_review_benchmark.py     ← OpenRouter, N models in parallel
        ↓
   results.json + results/<model>.md
        ↓
   (opt.) Claude subagents      ← Opus / Sonnet / Haiku via Claude Code
        ↓
   results_claude_subagent/<model>.md
        ↓
2. aggregate_findings.py parse  ← parses .md → findings.json (no LLM)
        ↓
   findings.json
        ↓
3. Claude in chat (default)     ← clusters findings → clusters.json
   or llm_judge.py cluster      ← (alt: OpenRouter judge)
        ↓
   clusters.json
        ↓
4. aggregate_findings.py render ← findings + clusters → worklist.md (no LLM)
        ↓
   worklist.md
        ↓
5. Claude in chat (default)     ← per-cluster verdict drafts
   or llm_judge.py adjudicate   ← (alt: OpenRouter judge → verdicts.draft.md)
        ↓
   verdicts.md  (human-approved)
        ↓
6. compute_metrics.py           ← metrics + leaderboard (+ optional report)
        ↓
   worklist_judged.md + leaderboard.md
   (+ findings_report.md if --report)
```

**Design principle:** the bulk Python scripts (parsing, rendering, metrics)
make zero LLM calls. Anything that needs reasoning (clustering, adjudication)
is delegated to Claude in the current chat session — typically free under a
Claude subscription. OpenRouter spend is confined to step 1, where
third-party models are exercised.

For users without Claude Code there is an optional `llm_judge.py` script
that runs the clustering and adjudication steps via OpenRouter with a
configurable judge model. The output is a **draft** (`verdicts.draft.md`)
that a human still reviews and approves before the metrics step. See
[Optional: automated drafts via OpenRouter](#optional-automated-drafts-via-openrouter)
below.

## Install

```bash
pip install -r requirements.txt
```

Get an OpenRouter API key at <https://openrouter.ai/keys>, then:

```powershell
$env:OPENROUTER_API_KEY = "..."   # PowerShell
```
```bash
export OPENROUTER_API_KEY=...     # bash
```

## Usage

### 1. Run the benchmark via OpenRouter

```bash
python code_review_benchmark.py path/to/some.diff \
  -c path/to/file1.cs \
  -c path/to/file2.cs \
  -o runs/<run-id>/results.json
```

Produces:
- `results.json` — metadata + raw responses from every model
- `results/<model>.md` — one markdown review per model

**Model list:** edit `models.json` (display name → OpenRouter model id), or
point to a different file with `--models-file PATH`. The repository ships a
default list to get you started; trim it to suit your OpenRouter quota and
which families you want to compare.

**Prompt:** the prompt template lives in `prompts/`. The default is
`prompts/review.en.txt`; an alternative `prompts/review.ru.txt` is included
as an example of localising the body while keeping English field markers
(`Findings:`, `Location:`, …). Override with `--prompt PATH`. The template
uses two placeholders: `{diff}` and `{context_block}`.

### 2. (Optional) Run Claude subagents

Dispatch Claude subagents (Opus / Sonnet / Haiku) from your chat and save
their reviews to `results_claude_subagent/Claude_<Model>.md`, using the same
heading format the parser expects (`1) [severity: ...] ...` or
`### 1) [...]`).

This step exists because Claude is not on OpenRouter for everyone, and
running it through your existing Claude Code subscription is typically free.

### 3. Parse findings

```bash
python aggregate_findings.py parse \
  --results-dir results \
  --subagent-dir results_claude_subagent \
  -o findings.json
```

### 4. Cluster findings (Claude in chat or `llm_judge.py cluster`)

Have Claude read `findings.json`, group findings by underlying problem, and
write `clusters.json`:

```json
{
  "clusters": [
    {"id": 1, "topic": "...", "consensus_severity": "major", "members": [<int idx>]}
  ]
}
```

Recipe for Claude in chat: open Claude Code in the run folder and ask:
> Read `findings.json`. Group the findings by the same underlying problem
> (use `prompts/cluster.en.txt` as the rubric). Write the result to
> `clusters.json` in the schema shown there.

Or run the optional automated path:

```bash
python llm_judge.py cluster \
  --findings runs/<id>/findings.json \
  -o runs/<id>/clusters.json \
  --judge-model openai/gpt-5.5
```

### 5. Render the worklist

```bash
python aggregate_findings.py render \
  --findings findings.json \
  --clusters clusters.json \
  -o worklist.md
```

### 6. Adjudicate (Claude in chat or `llm_judge.py adjudicate`)

For each cluster, a reviewer reads the source at the cited location and
assigns a verdict: `real | smell | nit | wrong`, with a short reason. The
result is `verdicts.md`:

```
## Cluster 1
- Verdict: real
- Confidence: high
- Reason: <one line>

## Cluster 2
...
```

**The human owns the final verdict.** Both paths below produce a draft —
review and override before saving as `verdicts.md`.

Recipe for Claude in chat: open Claude Code in the run folder, point it at
`worklist.md`, and ask:
> For each cluster in `worklist.md`, read the source at the cited
> `Location:` and assign a verdict using the rubric in `prompts/judge.en.txt`.
> Write the verdicts to `verdicts.md` in the schema shown there.

Or run the optional automated path (produces `verdicts.draft.md`, which you
review and rename to `verdicts.md`):

```bash
python llm_judge.py adjudicate \
  --clusters runs/<id>/clusters.json \
  --findings runs/<id>/findings.json \
  --repo-path /path/to/repo \
  --context-lines 50 \
  -o runs/<id>/verdicts.draft.md \
  --judge-model openai/gpt-5.5
```

The draft includes a "Needs human attention" preamble that flags
low-confidence calls, severity-dissent clusters, and singletons (one model
only) so you can prioritise what to look at.

### 7. Compute metrics

```bash
python compute_metrics.py \
  --verdicts verdicts.md \
  --findings findings.json \
  --clusters clusters.json \
  --results results.json
```

Produces:
- `worklist_judged.md` — the worklist with checkboxes filled and judge notes
  inlined (useful for human verification, especially low-confidence calls)
- `leaderboard.md` — per-model precision, recall, hallucination rate, $/real

### 8. (Optional) Findings report skeleton

Pass `--report runs/<id>/findings_report.md` to `compute_metrics.py` to
render a narrative-style report skeleton with auto-filled tables (real bugs,
who-found-what, severity calibration, cost/value) and `<!-- TODO -->` blocks
for the prose sections (singleton commentary, patterns, methodology
takeaway). The template lives at `templates/findings_report.template.md`.

```bash
python compute_metrics.py \
  --verdicts runs/<id>/verdicts.md \
  --findings runs/<id>/findings.json \
  --clusters runs/<id>/clusters.json \
  --results  runs/<id>/results.json \
  --leaderboard runs/<id>/leaderboard.md \
  --report      runs/<id>/findings_report.md
```

Then a reviewer (you or a Claude subagent in chat) fills in the prose
blocks. This is the document that ties the run together for a paper or a
team writeup.

## Optional: automated drafts via OpenRouter

The default workflow uses Claude in chat for the two LLM-driven steps
(clustering, adjudication). For users without Claude Code, or for
reproducibility studies (running multiple judges and comparing), the
optional `llm_judge.py` script does both via OpenRouter with a configurable
judge model:

```bash
# Step 4 alt
python llm_judge.py cluster \
  --findings runs/<id>/findings.json \
  -o runs/<id>/clusters.json \
  --judge-model openai/gpt-5.5

# Step 6 alt — produces verdicts.draft.md, NOT verdicts.md
python llm_judge.py adjudicate \
  --clusters runs/<id>/clusters.json \
  --findings runs/<id>/findings.json \
  --repo-path /path/to/repo \
  --context-lines 50 \
  -o runs/<id>/verdicts.draft.md \
  --judge-model openai/gpt-5.5
```

The judge model and clustering rubric are not the same as the methodology
itself — they live in `prompts/cluster.en.txt` and `prompts/judge.en.txt`.
Tune them to your codebase before relying on the output.

**Trade-offs to be aware of:**
- LLM-as-judge has known biases (position, length, self-preference). If your
  judge is from the same family as a model under review, that model gets a
  small fork. Run multiple judges if reproducibility matters.
- The judge sees only ±N lines around the cited `Location`, not the whole
  file or its callers. For bugs whose verdict depends on caller invariants,
  the judge will (correctly) mark `Confidence: low` and the human reviewer
  has to walk the call sites.
- The methodology principle "human owns the final call" still holds: the
  output is named `verdicts.draft.md` so it cannot be silently fed into
  metrics. You rename it to `verdicts.md` only after review.

## Verdict categories

- **real** — a genuine bug with production impact: crash, wrong result,
  degraded behaviour on typical inputs, race, leak, data loss
- **smell** — code health, won't crash: duplication, bad names, missing docs,
  DRY violations, asymmetric APIs
- **nit** — pure style: whitespace, micro-optimisation, idiomatic preference
- **wrong** — the model is mistaken: the issue doesn't exist, the code was
  misread, or the suggestion doesn't apply

Tie-breaker: real/smell → smell, smell/nit → nit, smell/wrong → re-check,
otherwise smell.

## Format compliance

Models don't always follow the response format exactly. `aggregate_findings.py`
ships with an `ISSUE_RE` regex that tolerates the common deviations
(`**bold**` decoration, `1.` instead of `1)`, severity wrapped in markdown,
etc.) and a companion `FORMAT_NOTES = {}` dictionary you can populate to
annotate models whose output parses but with caveats — those notes appear
next to the model in `worklist.md` so reviewers know to give those findings
extra scrutiny. The dictionary ships empty; populate it from your own runs.
See [CONTRIBUTING.md](CONTRIBUTING.md#format-compliance-notes) for the
mechanics.

Both parsers (`code_review_benchmark.py` for live API responses, and
`aggregate_findings.py` for parsing markdown reviews) expect English field
markers as defined in `prompts/review.en.txt`: `Findings:`, `Location:`,
`Why it matters:`, `Evidence:`, `Recommendation:`, plus a
`[severity: blocker/major/minor/nit]` tag. If you replace the markers, update
the parser regexes accordingly.

## Repository layout

```
ai-code-review-benchmark/
├── README.md
├── README.ru.md
├── LICENSE
├── requirements.txt
├── code_review_benchmark.py        ← OpenRouter runner
├── aggregate_findings.py           ← parser + worklist renderer (no LLM)
├── compute_metrics.py              ← metrics + leaderboard + report (no LLM)
├── llm_judge.py                    ← optional: automated cluster+adjudicate drafts
├── models.json                     ← default model list (override with --models-file)
├── prompts/
│   ├── review.en.txt               ← reviewer prompt (step 1)
│   ├── review.ru.txt               ← Russian-body example (English markers)
│   ├── cluster.en.txt              ← clustering rubric (step 4 — used by llm_judge or Claude in chat)
│   └── judge.en.txt                ← adjudication rubric (step 6)
├── templates/
│   └── findings_report.template.md ← skeleton for compute_metrics.py --report
└── runs/                           ← gitignored — your local benchmark runs
    └── <run-id>/                   ← one run = one folder
        ├── input.diff              ← the diff under review (for reproducibility)
        ├── results.json            ← OpenRouter raw output
        ├── results/                ← per-model .md (OpenRouter)
        ├── results_claude_subagent/← per-model .md (Claude subagents)
        ├── findings.json           ← parsed findings
        ├── clusters.json           ← clusters (Claude in chat or llm_judge.py cluster)
        ├── worklist.md             ← worklist for human review
        ├── verdicts.draft.md       ← (optional) draft from llm_judge.py adjudicate
        ├── verdicts.md             ← per-cluster verdicts (human-approved)
        ├── worklist_judged.md      ← worklist + verdicts merged
        ├── leaderboard.md          ← per-model metrics
        ├── findings_report.md      ← (optional) narrative report (--report)
        └── run.log                 ← stdout of the run
```

**Run id convention:** `runs/<short-id>/` — the id can be a ticket
(`PROJ-1234`), a feature name (`auth-refactor`), or a date
(`2026-05-09-deepseek-only`). Keep all artefacts of a single run inside one
folder; pass `--output runs/<id>/...` to the scripts.

`runs/` is gitignored by default so that runs against private code never leak
into the public repository. Remove that line from `.gitignore` only if a
specific run is fully public.

## Files

### Scripts (in this repo)

| File | Format / what it is | Producer |
|---|---|---|
| `code_review_benchmark.py` | Python — OpenRouter runner; calls LLMs (step 1) | this repo |
| `aggregate_findings.py` | Python — parser + worklist renderer; no LLM calls | this repo |
| `compute_metrics.py` | Python — metrics + leaderboard + (optional) findings_report; no LLM calls | this repo |
| `llm_judge.py` | Python — optional; calls OpenRouter for steps 4 + 6 | this repo |
| `models.json` | JSON — `{display_name: openrouter_model_id}`; keys starting with `_` are comments | this repo (override with `--models-file`) |
| `prompts/review.en.txt` · `review.ru.txt` | Step 1 prompt; placeholders `{diff}` and `{context_block}` | this repo (override with `--prompt`) |
| `prompts/cluster.en.txt` | Step 4 rubric; placeholder `{findings_block}`. Edit the TODO block to encode your clustering rules. | this repo |
| `prompts/judge.en.txt` | Step 6 rubric; placeholders `{cluster_id}`, `{cluster_topic}`, `{cluster_severity}`, `{cluster_findings}`, `{source_excerpt}`, `{source_status}`. Edit the TODO block to encode your verdict criteria. | this repo |
| `templates/findings_report.template.md` | Markdown skeleton with `{TOKEN}` placeholders for auto-filled tables and `<!-- TODO -->` blocks for human prose | this repo (override with `--report-template`) |

### Run artefacts (under `runs/<run-id>/`)

Everything below is created by the pipeline for one run.

| File | Schema | Producer |
|---|---|---|
| `input.diff` | Unified diff text | user (`git diff > input.diff`) |
| `results.json` | JSON — `{ meta: {diff_file, diff_size_chars, context_files, ...}, results: { <model>: {status, content, issues, issues_count, usage: {prompt_tokens, completion_tokens, total_tokens}, elapsed_sec} } }` | `code_review_benchmark.py` |
| `results/<model>.md` | Markdown — `Findings:` block of numbered items: `N) [severity: blocker\|major\|minor\|nit] summary` followed by `- Location:`, `- Why it matters:`, `- Evidence:`, `- Recommendation:` | `code_review_benchmark.py` |
| `results_claude_subagent/<model>.md` | Markdown — same shape as above | Claude subagents (dispatched manually from chat) |
| `findings.json` | JSON — `{ issues: [{model, severity, summary, location, why_it_matters, evidence, recommendation}] }` | `aggregate_findings.py parse` |
| `clusters.json` | JSON — `{ clusters: [{id, topic, consensus_severity, members: [<int idx into issues[]>]}] }` | Claude in chat — or `llm_judge.py cluster` |
| `worklist.md` | Markdown — clusters with `[ ]` checkboxes (`real` / `smell` / `nit` / `wrong`) ready for human labelling | `aggregate_findings.py render` |
| `verdicts.draft.md` | Markdown — same shape as `verdicts.md` plus a "Needs human attention" preamble. **Optional**, for review only — rename to `verdicts.md` after editing. | `llm_judge.py adjudicate` |
| `verdicts.md` | Markdown — for each cluster: `## Cluster N` then `- Verdict:`, `- Confidence:`, `- Reason:` | Claude in chat (per-cluster, source-aware) — or human review of `verdicts.draft.md` |
| `worklist_judged.md` | Markdown — `worklist.md` with `[x]` filled in and judge notes inlined | `compute_metrics.py` |
| `leaderboard.md` | Markdown — per-model precision / recall / hallucination rate / $/real | `compute_metrics.py` |
| `findings_report.md` | Markdown — narrative report skeleton with auto-filled tables (real bugs, who found what, severity calibration, cost/value) plus `<!-- TODO -->` blocks for prose | `compute_metrics.py --report` (then human prose) |
| `cost_estimates.json` | JSON — `{ <model>: {usd, source, kind: "actual"\|"estimated"\|"estimated_anthropic"} }` (optional, for `$/real`) | user (manual; from OpenRouter dashboard or public per-token tariffs) |
| `run.log` | Plain text — stdout of step 1 | `code_review_benchmark.py` (redirect) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a model, change the
prompt, or update the parser regexes when you change response markers.
