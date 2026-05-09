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
3. Claude in current chat       ← clusters findings, writes clusters.json
        ↓
   clusters.json
        ↓
4. aggregate_findings.py render ← findings + clusters → worklist.md (no LLM)
        ↓
   worklist.md
        ↓
5. Claude in current chat       ← per-cluster verdict: real / smell / nit / wrong
        ↓
   verdicts.md
        ↓
6. compute_metrics.py           ← per-model metrics + leaderboard (no LLM)
        ↓
   worklist_judged.md + leaderboard.md
```

**Design principle:** the Python scripts make zero LLM calls. Anything that
needs reasoning (clustering, adjudication) is delegated to Claude in the
current chat session — typically free under a Claude subscription. OpenRouter
spend is confined to step 1, where third-party models are exercised.

## Install

```bash
pip install -r requirements.txt
```

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

### 4. Cluster findings (Claude in chat)

Have Claude read `findings.json`, group findings by underlying problem, and
write `clusters.json`:

```json
{
  "clusters": [
    {"id": 1, "topic": "...", "consensus_severity": "major", "members": [<int idx>]}
  ]
}
```

### 5. Render the worklist

```bash
python aggregate_findings.py render \
  --findings findings.json \
  --clusters clusters.json \
  -o worklist.md
```

### 6. Adjudicate (Claude in chat)

For each cluster, Claude reads the source at the cited location and assigns a
verdict: `real | smell | nit | wrong`, with a short reason. The result is
`verdicts.md`:

```
## Cluster 1
- Verdict: real
- Confidence: high
- Reason: <one line>

## Cluster 2
...
```

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
├── compute_metrics.py              ← metrics + leaderboard (no LLM)
├── models.json                     ← default model list (override with --models-file)
├── prompts/
│   ├── review.en.txt               ← default prompt template
│   └── review.ru.txt               ← Russian-body example (English markers)
└── runs/                           ← gitignored — your local benchmark runs
    └── <run-id>/                   ← one run = one folder
        ├── input.diff              ← the diff under review (for reproducibility)
        ├── results.json            ← OpenRouter raw output
        ├── results/                ← per-model .md (OpenRouter)
        ├── results_claude_subagent/← per-model .md (Claude subagents)
        ├── findings.json           ← parsed findings
        ├── clusters.json           ← Claude-produced clusters
        ├── worklist.md             ← worklist for human review
        ├── verdicts.md             ← per-cluster verdicts
        ├── worklist_judged.md      ← worklist + verdicts merged
        ├── leaderboard.md          ← per-model metrics
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
| `code_review_benchmark.py` | Python — OpenRouter runner; calls LLMs | this repo |
| `aggregate_findings.py` | Python — parser + worklist renderer; no LLM calls | this repo |
| `compute_metrics.py` | Python — metrics + leaderboard; no LLM calls | this repo |
| `models.json` | JSON — `{display_name: openrouter_model_id}`; keys starting with `_` are comments | this repo (override with `--models-file`) |
| `prompts/review.en.txt` · `review.ru.txt` | Text template — placeholders `{diff}` and `{context_block}` | this repo (override with `--prompt`) |

### Run artefacts (under `runs/<run-id>/`)

Everything below is created by the pipeline for one run.

| File | Schema | Producer |
|---|---|---|
| `input.diff` | Unified diff text | user (`git diff > input.diff`) |
| `results.json` | JSON — `{ meta: {diff_file, diff_size_chars, context_files, ...}, results: { <model>: {status, content, issues, issues_count, usage: {prompt_tokens, completion_tokens, total_tokens}, elapsed_sec} } }` | `code_review_benchmark.py` |
| `results/<model>.md` | Markdown — `Findings:` block of numbered items: `N) [severity: blocker\|major\|minor\|nit] summary` followed by `- Location:`, `- Why it matters:`, `- Evidence:`, `- Recommendation:` | `code_review_benchmark.py` |
| `results_claude_subagent/<model>.md` | Markdown — same shape as above | Claude subagents (dispatched manually from chat) |
| `findings.json` | JSON — `{ issues: [{model, severity, summary, location, why_it_matters, evidence, recommendation}] }` | `aggregate_findings.py parse` |
| `clusters.json` | JSON — `{ clusters: [{id, topic, consensus_severity, members: [<int idx into issues[]>]}] }` | Claude in chat (one-shot) |
| `worklist.md` | Markdown — clusters with `[ ]` checkboxes (`real` / `smell` / `nit` / `wrong`) ready for human labelling | `aggregate_findings.py render` |
| `verdicts.md` | Markdown — for each cluster: `## Cluster N` then `- Verdict:`, `- Confidence:`, `- Reason:` | Claude in chat (per-cluster source-aware adjudication) |
| `worklist_judged.md` | Markdown — `worklist.md` with `[x]` filled in and judge notes inlined | `compute_metrics.py` |
| `leaderboard.md` | Markdown — per-model precision / recall / hallucination rate / $/real | `compute_metrics.py` |
| `cost_estimates.json` | JSON — `{ <model>: {usd, source, kind: "actual"\|"estimated"\|"estimated_anthropic"} }` (optional, for `$/real`) | user (manual; from OpenRouter dashboard or public per-token tariffs) |
| `run.log` | Plain text — stdout of step 1 | `code_review_benchmark.py` (redirect) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a model, change the
prompt, or update the parser regexes when you change response markers.
