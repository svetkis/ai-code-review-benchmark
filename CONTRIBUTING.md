# Contributing

This is a small, opinionated tool. The three changes you'll most often want to
make are: **add a model**, **change the prompt**, or **change the response
markers** the parsers depend on. Each is described below.

If you spot a bug or want to propose a methodology change, open an issue first
so we can discuss the shape of the change before code is written.

## Add a model

Models are listed in [`models.json`](models.json) as
`display_name → openrouter_model_id`:

```json
"DeepSeek V4 Pro":    "deepseek/deepseek-v4-pro",
```

To add a new model:

1. Find its OpenRouter id at <https://openrouter.ai/models>.
2. Add a line to `models.json`. The display name becomes the filename in
   `results/` (sanitised: non-`\w-` chars → `_`), and the column label in the
   leaderboard. Keep it short and human-readable.
3. (Optional) If you want to keep your default list small, point the runner
   at a custom file: `--models-file path/to/my-models.json`.

Keys starting with `_` are treated as comments and skipped — use that for
section dividers or notes.

## Change the prompt

The prompt template lives in [`prompts/`](prompts/). The default is
`prompts/review.en.txt`. To use a different one for a single run:

```bash
python code_review_benchmark.py input.diff --prompt prompts/my-prompt.txt -c file.cs
```

A template must include both placeholders:

- `{diff}` — replaced with the unified diff text
- `{context_block}` — replaced with the formatted full-file context (or `""`)

The body of the prompt can be in any language. **The field markers in the
model's response (`Findings:`, `Location:`, `Why it matters:`, `Evidence:`,
`Recommendation:`, `[severity: ...]`) must stay in English** — otherwise the
parsers won't recognise them. See `prompts/review.ru.txt` for a worked example
of a localised body with English markers.

If you want to translate the markers themselves too, see the next section.

## Change the response markers (regex)

If you replace the English markers — say, you want `Местоположение:` instead
of `Location:` — you must update the parser regexes. There are three:

| File | Constant | What it matches |
|---|---|---|
| `code_review_benchmark.py` | `ISSUE_HEADER_RE` (~line 176) | `N) [severity: ...] summary` headers in live API responses |
| `code_review_benchmark.py` | `_FIELD_START_RE` (~line 229) | `- Field: value` field lines in live API responses |
| `aggregate_findings.py` | `ISSUE_RE` (~line 41) | numbered headers across saved markdown reviews; tolerates `**bold**` decoration and `1.` instead of `1)` |

`aggregate_findings.py` also has a `FIELDS` list that names the four fields
extracted from each finding (`Location`, `Why it matters`, `Evidence`,
`Recommendation`). Update both `FIELDS` and the regex when renaming markers.

Two regexes downstream are **not** prompt-coupled and should be left alone
when changing markers — they parse human-/Claude-produced files, not LLM
output:

- `compute_metrics.py:VERDICT_BLOCK_RE` parses `verdicts.md`
- `compute_metrics.py:CLUSTER_HEADER_RE` parses cluster headers in
  `worklist.md`

## Format compliance notes

`aggregate_findings.py` has a `FORMAT_NOTES = {}` dictionary. If during your
runs a model parses but with caveats (e.g. uses `**bold**` for severity, or
`1.` instead of `1)`), add an entry: `"Display_Name": "loose (...)"`. The
note is shown next to the model in `worklist.md` so reviewers know to give
those findings extra scrutiny. The dictionary ships empty — populate it from
your own observations.

## Tune the clustering and judge prompts

The optional `llm_judge.py` script delegates the methodology decisions to
two prompt files, both of which ship with TODO blocks you should fill in
before relying on the output:

- `prompts/cluster.en.txt` — what counts as "the same problem"; how to
  pick `consensus_severity` when models disagree; whether to keep
  singletons or drop them. Replace the `<!-- TODO ... -->` block with 5–10
  lines of explicit rules drawn from your own runs.
- `prompts/judge.en.txt` — the bar for `real / smell / nit / wrong`, and
  when to mark `Confidence: low`. Same shape: replace the TODO block.

The placeholders the runner fills in are documented at the top of each
template. **Do not change the placeholder names** unless you also adjust
`llm_judge.py` (it passes them by string replacement, not `.format()`).

When you want to compare judges, override the model:

```bash
python llm_judge.py adjudicate --judge-model anthropic/claude-opus-4 ... -o verdicts.opus.md
python llm_judge.py adjudicate --judge-model openai/gpt-5.5         ... -o verdicts.gpt55.md
```

Cross-judge agreement is one of the more useful sanity checks for the
methodology — if two strong judges disagree on a cluster, that's a
high-attention case for the human reviewer.

## Customise the findings report template

`compute_metrics.py --report` substitutes data into
`templates/findings_report.template.md`. The placeholders are simple
`{TOKEN}` strings; the data side is rendered by `render_findings_report`
in `compute_metrics.py`.

To add a new auto-filled section: add a `{TOKEN}` to the template, and add
the corresponding entry to the `substitutions` dict in
`render_findings_report`. To add a new data computation, write a small
helper next to the existing ones (`_real_bugs_table`,
`_per_model_real_table`, etc.) — they all take `clusters`, `issues`,
`verdicts` (and optionally `metrics`/`cost_data`) and return a Markdown
fragment.

To use a different report shape entirely, point `--report-template` at
your own file. The template is just Markdown with `{TOKEN}` placeholders;
unrecognised placeholders are left as-is, so you can iterate without
touching the script.

## Filing issues / PRs

- One issue per topic, with a minimal repro (a small `input.diff` and the
  command you ran is ideal).
- For methodology changes (new metric, different clustering rule, alternate
  verdict scheme), describe the *why* before the *what* — the methodology is
  more load-bearing than any single piece of code.
- Tests aren't required yet, but a handful of fixture-based unit tests for
  the parsers would be welcome (this is task B4.2 in the backlog).
