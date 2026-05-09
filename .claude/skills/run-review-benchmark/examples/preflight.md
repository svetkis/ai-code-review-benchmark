# Preflight confirm-block — format sample

This file shows the **format** of the confirm-block the skill prints before
running step 1 (the only paid step). It is not a real run — the skill must
fill every value below from the actual pre-flight state (run-id, diff path
and size, context files, the resolved `models.json` count, the prompt path,
and the planned output path).

Use this file as a template. Do not copy the literal values; substitute them.

---

## About to run benchmark step 1 (paid: OpenRouter)

- run-id:        BANKIDEAS-2113
- diff:          runs/BANKIDEAS-2113/input.diff (4.2 KB, 87 lines, 3 files)
- context files: src/Foo.cs, src/Bar.cs
- models:        models.json (15 models)
- prompt:        prompts/review.en.txt
- output:        runs/BANKIDEAS-2113/results.json

If no context files were passed (`-c` is optional), write
`context files: (none)` so the field is never blank.

Reply 'go' (or 'yes', 'proceed', 'да', 'поехали', 'confirm') to start, or tell me what to change
(e.g. 'use only first 5 models', 'switch prompt to ru').
