---
name: run-review-benchmark
description: Run the LLM code-review benchmark pipeline end-to-end — fan a diff out to multiple models via OpenRouter, parse findings, build the worklist, compute the leaderboard. Use when the user says "benchmark this PR/branch/diff", "compare models on this code", asks how to use code_review_benchmark.py / aggregate_findings.py / compute_metrics.py, or wants to set up a new run-id under runs/.
---

# run-review-benchmark

## When to use this skill

Use this skill when the user wants to **measure** how several LLMs perform
on a concrete diff: trigger phrases include "benchmark this PR/branch/diff",
"compare models on this code", "run the benchmark on …", or any request to
operate `code_review_benchmark.py`, `aggregate_findings.py`, or
`compute_metrics.py` and produce a `leaderboard.md` under `runs/<id>/`.

Do **not** use this skill for:

- Reviewing a single PR for actual code-quality feedback — that is the
  `pr-review-toolkit:review-pr` skill.
- Methodology questions ("how should I weight precision vs recall?",
  "what counts as `real` vs `smell`?") — those will move to the
  `review-finding-card` skill once it lands. Until then, point at
  `prompts/judge.en.txt` and the README "Verdict categories" section.
- Adding or removing models from `models.json`, tweaking parser regexes,
  or fixing format-compliance notes — that is `CONTRIBUTING.md`.

## Pre-flight checklist

Run all four checks before printing the confirm-block. If any check fails,
**halt** with the indicated message and do not proceed to step 1.

**1. Are we in a clone of `code-review-benchmark`?** Check for
`code_review_benchmark.py` in cwd (`Test-Path code_review_benchmark.py` /
`test -f code_review_benchmark.py`). If absent, halt with
> "This skill must run from a clone of code-review-benchmark. `cd` into the
> repo root (the folder that contains `code_review_benchmark.py`) and
> try again."

**2. Is `OPENROUTER_API_KEY` set?** Read `$env:OPENROUTER_API_KEY`
(PowerShell) or `$OPENROUTER_API_KEY` (bash). If empty, halt with
> "OpenRouter API key is not set. Get one at <https://openrouter.ai/keys>, then:
> PowerShell: `$env:OPENROUTER_API_KEY = '...'`
> bash:       `export OPENROUTER_API_KEY=...`
> Re-run after setting it."

**3. Are Python deps installed?** Run `python -c "import requests"`. On
non-zero exit, halt with
> "Missing Python dependencies. Run: `pip install -r requirements.txt`."

**4. Is the model list resolvable?** If the user passed `--models-file PATH`,
check that PATH exists; otherwise default to `models.json` in cwd. If
missing, halt with
> "Model list not found at `<path>`. Either pass `--models-file <existing path>`
> or restore `models.json` (see CONTRIBUTING.md for the schema)."

If present, count the entries (keys not starting with `_`) and remember
the number for the confirm-block.

## Decide run-id

Pick the `runs/<id>/` slot **before** acquiring the diff so artefacts land
in one folder.

Selection rules, in priority order:

1. **User said it explicitly** (e.g. "run-id=auth-refactor",
   "call it BANKIDEAS-2113") — take it verbatim.
2. **Ticket pattern in branch name or recent git log** — if the current
   branch or `git log -1 --format=%s` contains something matching
   `[A-Z]+-\d+`, propose it (e.g. "Use `BANKIDEAS-2113`?").
3. **Fallback** — `YYYY-MM-DD-<short-slug>`, where `<short-slug>` is two
   or three words distilled from the user's prompt
   (e.g. `2026-05-09-deepseek-test`).

Validation: the id must match `^[\w\-\.]+$`, be ≤ 50 characters, and
contain no slashes. If a candidate fails, normalise it (replace bad
characters with `-`) and confirm with the user before using.

**Conflict handling — idempotency.** If `runs/<id>/` already exists and is
non-empty, do **not** overwrite. Ask:

> "`runs/<id>/` already exists with `<list of present artefacts>`. What
> would you like to do?
> - **continue** — resume from the next missing step
> - **restart** — wipe the folder and start over
> - **pick new id** — give a new run-id"

**Resume rubric** — if the user picks `continue`, locate the first
missing artefact in this order and resume from the corresponding step:

| Missing | Resume from |
|---|---|
| `input.diff` | Acquire input.diff (then re-print confirm-block before step 1) |
| `results.json` | Step 1 (re-print confirm-block first — paid step) |
| `findings.json` | Step 2 |
| `clusters.json` | Step 3 — Pause: clustering |
| `worklist.md` | Step 4 |
| `verdicts.md` | Step 5 — Pause: adjudication |
| `worklist_judged.md` or `leaderboard.md` | Step 6 |

If the missing step is step 1 (paid), always re-print the confirm-block
before resuming, even on `continue`. The user's prior `go` does not carry
across invocations.

Create the folder when ready: `New-Item -ItemType Directory -Path runs/<id> -Force`
(PowerShell) / `mkdir -p runs/<id>` (bash).

## Acquire input.diff

Choose the source by what the user said. If multiple readings are
plausible, ask before guessing.

| User said | Command |
|---|---|
| "PR #N", `https://github.com/.../pull/N` | `gh pr diff N > runs/<id>/input.diff` |
| "MR !N", `https://gitlab.com/.../merge_requests/N` | `glab mr diff N > runs/<id>/input.diff` |
| "branch", "my branch", "current branch vs main" | `git diff $(git merge-base HEAD $BASE)...HEAD > runs/<id>/input.diff` (resolve `$BASE` first — see "Default branch detection" below) |
| "uncommitted", "what I've changed" | `git diff HEAD > runs/<id>/input.diff` |
| Path to a file | `Copy-Item <path> runs/<id>/input.diff` (PowerShell) / `cp <path> runs/<id>/input.diff` (bash) |
| Anything else / ambiguous | Ask, listing the options above |

**Cross-repo PR/MR URLs.** If the URL points to a different repository
than the current cwd's origin (e.g. user is in `code-review-benchmark`
and asks for a PR from `acme/web`), pass `--repo owner/name` to `gh`
(or `--repo owner/name` to `glab`) to override the default cwd-relative
resolution: `gh pr diff N --repo acme/web > runs/<id>/input.diff`.

**Default branch detection** (for the "branch" row): run
`git symbolic-ref --short refs/remotes/origin/HEAD` — it returns
`origin/main` or `origin/master` directly. Use that result as `$BASE` and
substitute it into the table command above (so the final command becomes
e.g. `git diff $(git merge-base HEAD origin/main)...HEAD > runs/<id>/input.diff`).
If `git symbolic-ref` fails (no `origin/HEAD` set), ask the user which
base branch to diff against rather than guessing.

**Pre-conditions per branch:**

- `gh` row → `gh auth status` must succeed; otherwise halt:
  > "GitHub CLI is not authenticated. Run `gh auth login`, then re-try."
- `glab` row → `glab auth status` must succeed; otherwise halt:
  > "GitLab CLI is not authenticated. Run `glab auth login`, then re-try."
- `git diff` rows → cwd must be inside a git repo; the resolved base
  branch must exist as a remote ref.
- File-path row → the path must exist; if not, halt and ask the user to
  re-supply.

**Sanity check after acquisition (always).** Read size and line count.
PowerShell: `$f = Get-Item runs/<id>/input.diff; $lines = (Get-Content $f | Measure-Object -Line).Lines; "$($f.Length) bytes, $lines lines"`.
bash: `wc -c -l < runs/<id>/input.diff`. Then:

- Zero bytes → **halt**:
  > "The diff is empty. Did you mean a different source? (uncommitted vs
  > branch vs PR)"
- Fewer than 5 lines, or larger than 2 MB → **warn and confirm**:
  > "This diff is `<size>` (`<lines>` lines). That's `<unusually small | unusually
  > large>`. Is this the diff you meant to benchmark?"
  Wait for an explicit yes before continuing. Do not halt.

## Confirm before paid step

Step 1 is the **only paid** step (OpenRouter API spend, scaled by
`N models × diff size`). Always pause here.

**Format:** print a confirm-block in the form shown in
[`examples/preflight.md`](examples/preflight.md), with every value
filled in from the live pre-flight state (run-id, diff path + size +
line count + file count, list of `-c` context files, model list path +
count, prompt path, output path).

**Do not start step 1 until the user replies "go"** (or a clear semantic
equivalent: `да`, `поехали`, `confirm`, `yes`, `proceed`). Treat anything
else as a request to revise.

If the user requests changes ("use only the first 5 models", "switch
prompt to ru", "drop the second context file"):

1. Apply the change (adjust the planned command-line / `--models-file` /
   `--prompt`).
2. Re-print the confirm-block with the new values.
3. Wait for `go` again.

Loop until `go`. If the user says `cancel` / `stop` / `abort`, halt and
**leave** the run folder, `input.diff`, and any other prepared artefacts
on disk — they make resuming a one-line affair next time.

## Step 1 — Run models

Run after `go`. This is the paid step.

```bash
python code_review_benchmark.py runs/<id>/input.diff \
  -c <ctx1> -c <ctx2> ... \
  -o runs/<id>/results.json \
  [--models-file PATH] \
  [--prompt PATH]
```

Pass `--models-file` and `--prompt` only if the user chose non-defaults
during the confirm loop.

**Expected artefacts on success:**

- `runs/<id>/results.json` — metadata + raw responses from every model
- `runs/<id>/results/<model>.md` — one markdown file per model in
  `models.json`

Per-model status is printed to stdout in the form
`[N/M] <name> (<model_id>)... OK — K findings — Ts` on success or
`[N/M] <name> (<model_id>)... FAIL — <reason>` on failure. Use those
exact tokens (`OK` / `FAIL`, uppercase) when counting outcomes.

**Failure modes:**

- **One model fails** (timeout / 401 / rate-limit on a single id) — the
  script is tolerant: it logs the error in `results.json` for that model
  and keeps going. Do not panic, do not retry.
- **All models fail with 401** — the key is revoked or wrong. **Halt**:
  > "Every model returned 401. Re-check `OPENROUTER_API_KEY` (the key may
  > be revoked or have an empty quota); fix it, then re-run."
- **`models.json` is malformed** (script exits before any call) — halt
  and surface the script's stderr verbatim. Do not auto-fix.

After completion, summarise inline:
> "Step 1 done: K of N models returned successfully. Failed: `<list>`.
> Moving on to step 2 (parsing)."

Then run step 2 immediately, no pause.

## Step 2 — Parse findings

```bash
python aggregate_findings.py parse \
  --results-dir runs/<id>/results \
  -o runs/<id>/findings.json
```

**Expected artefact:** `runs/<id>/findings.json` with the schema

```
{ issues: [ {model, severity, summary, location, why_it_matters,
             evidence, recommendation} ] }
```

**Failure modes:**

- **A model's review doesn't parse** — the script is tolerant via
  `ISSUE_RE` and `FORMAT_NOTES`; that model's findings are best-effort
  and a note is attached in `worklist.md` later. Warn but continue.
- **`runs/<id>/results/` is empty** (step 1 produced nothing) — **halt**.
  Do not run step 4 against an empty `findings.json`. Ask whether to
  re-run step 1 or pick a new run-id.

No pause. Continue straight into the clustering pause.

## Step 3 — Pause: clustering

Clustering needs an LLM to group findings by underlying problem. This is
**out of scope** for the skill (no methodology embedded). Delegate to one
of the two paths below.

> **Note (for the future):** once the `review-finding-card` skill ships,
> this section collapses to `→ see review-finding-card skill`. Until then
> the literal prompt-block lives here, mirroring README §3.

**Path A — Inline in this chat (recommended).** Speak the literal prompt
from README §3 into the conversation (qualify the file paths with the
`runs/<id>/` prefix, since cwd is the repo root rather than the run
folder):

> Read `findings.json`. Group the findings by the same underlying problem
> (rubric — `prompts/cluster.en.txt`). Write the result to `clusters.json`.

**Path B — Automated draft via `llm_judge.py`** (for users without
Claude Code, or for reproducibility):

```bash
python llm_judge.py cluster \
  --findings runs/<id>/findings.json \
  -o runs/<id>/clusters.json
```

If you want a specific judge model, pass `--judge-model <openrouter-id>`.
Otherwise the script default applies.

**Resume condition.** Before continuing to step 4, verify
`runs/<id>/clusters.json`:

1. The file exists.
2. It parses as JSON with the shape
   `{clusters: [{id, topic, consensus_severity, members: [int]}]}`.

If the schema is wrong, **halt**, print the expected shape, and ask the
user to regenerate. If the file simply hasn't appeared after the
delegation prompt, prompt **once** more — do not loop indefinitely.

## Step 4 — Render worklist

```bash
python aggregate_findings.py render \
  --findings runs/<id>/findings.json \
  --clusters runs/<id>/clusters.json \
  -o runs/<id>/worklist.md
```

**Expected artefact:** `runs/<id>/worklist.md` — a checklist with `[ ]`
boxes per cluster, ready for the human to label.

**Failure modes:**

- **`clusters.json` references finding indices that don't exist** — the
  script will fail. Halt, surface stderr, ask the user to regenerate
  `clusters.json` (likely a copy-paste error or a stale findings file).
- **Worklist comes out empty** (zero clusters) — warn but continue. The
  leaderboard will still compute, just with all-zero counts.

No pause. Continue straight into the adjudication pause.

## Step 5 — Pause: adjudication

Symmetric to the clustering pause. Adjudication is methodology
(`review-finding-card` territory once that skill exists); the skill
delegates with the literal README §5 prompt.

> **Note (for the future):** once the `review-finding-card` skill ships,
> this section collapses to `→ see review-finding-card skill`.

**Path A — Inline in this chat (recommended).** From README §5 (qualify
file paths with `runs/<id>/`, since cwd is the repo root):

> For each cluster in `worklist.md` read the source at `Location:` and
> assign a verdict using the rubric in `prompts/judge.en.txt`. Write to
> `verdicts.md`.

**Path B — Automated draft via `llm_judge.py`** (output is
`verdicts.draft.md`; the human renames it to `verdicts.md` after review):

```bash
python llm_judge.py adjudicate \
  --clusters runs/<id>/clusters.json \
  --findings runs/<id>/findings.json \
  --repo-path /path/to/repo \
  --context-lines 50 \
  -o runs/<id>/verdicts.draft.md
```

If you want a specific judge model, pass `--judge-model <openrouter-id>`.
Otherwise the script default applies.

**Resume condition.** Before step 6, verify `runs/<id>/verdicts.md`:

1. The file exists (not `verdicts.draft.md` — the draft must be reviewed
   and renamed).
2. It parses into `## Cluster N` blocks, each with `Verdict:`,
   `Confidence:`, `Reason:` lines.
3. Every cluster id present in `clusters.json` is covered.

If clusters are missing from `verdicts.md`, **halt** and list exactly
which ones still need a verdict. If the file hasn't appeared after the
delegation prompt, ask **once** more — do not loop.

## Step 6 — Compute metrics

```bash
python compute_metrics.py \
  --verdicts runs/<id>/verdicts.md \
  --findings runs/<id>/findings.json \
  --clusters runs/<id>/clusters.json \
  --results runs/<id>/results.json
```

**Expected artefacts:**

- `runs/<id>/worklist_judged.md` — the worklist with `[x]` filled in and
  the judge's notes inlined per cluster (useful for verification, low-
  confidence calls especially).
- `runs/<id>/leaderboard.md` — the results table: precision, recall,
  hallucination rate, $/real per model.

**Failure modes:**

- **`verdicts.md` is missing clusters** — script halts before writing the
  leaderboard. Surface its list of missing clusters, halt the skill, ask
  the user to extend `verdicts.md`.
- **`usage.cost == null` for one or more models** — happens when a model
  is routed outside OpenRouter or OpenRouter didn't report cost. Warn and
  point the user at `cost_estimates.json` as the documented override.
  Keys are sanitised model names: `re.sub(r"[^\w\-]+", "_", name)`. Do
  not invent costs.

After success, surface the leaderboard inline (or its path) and offer
step 7 if the user hasn't already opted in or out:
> "Leaderboard written to `runs/<id>/leaderboard.md`. Want a narrative
> report (`--report`) too?"

## Step 7 (optional) — Narrative report

Trigger when the user explicitly asks for a writeup ("and make a report",
"need a writeup for the team", "include `--report`"). If the user said
"metrics only" earlier, skip — do not nag.

```bash
python compute_metrics.py \
  --verdicts runs/<id>/verdicts.md \
  --findings runs/<id>/findings.json \
  --clusters runs/<id>/clusters.json \
  --results  runs/<id>/results.json \
  --leaderboard runs/<id>/leaderboard.md \
  --report      runs/<id>/findings_report.md
```

**Expected artefact:** `runs/<id>/findings_report.md` — auto-filled
tables (real bugs, who-found-what, severity calibration, cost/value)
plus `<!-- TODO -->` blocks for human prose. The skeleton is
`templates/findings_report.template.md`.

After completion, point the user at the `<!-- TODO -->` blocks:
> "Report written to `runs/<id>/findings_report.md`. The `<!-- TODO -->`
> blocks are for your prose — the skill does not try to fill them."

The skill must not invent narrative or auto-fill those blocks. That is a
human writeup.

## Pointers

- `README.md` — the full pipeline picture, mermaid diagram, and the
  expanded prose for steps 3 and 5 (clustering and adjudication).
- `CONTRIBUTING.md` — how to add a model to `models.json`, how to tweak
  the parser regexes (`ISSUE_RE`), and the format-compliance notes
  mechanism (`FORMAT_NOTES`).
- `prompts/cluster.en.txt`, `prompts/judge.en.txt` — the rubrics that the
  pause-step delegations refer to. Edit before running on a new
  codebase.
- `templates/findings_report.template.md` — the skeleton step 7 fills in.
- `cost_estimates.json` — documented override for `usage.cost == null`
  (models routed outside OpenRouter).
- `review-finding-card` skill — **once it ships**, the clustering and
  adjudication pause sections collapse to a pointer at it. Until then,
  the literal prompts above are the source of truth.
