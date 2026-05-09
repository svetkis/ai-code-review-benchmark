# Findings Report — {RUN_ID}

<!-- TODO (1-2 sentences): brief framing — what PR is this, what was the motivation
     to benchmark code review on it, anything notable about the codebase. -->

## Numbers

- **{TOTAL_FINDINGS}** findings from **{TOTAL_MODELS}** models
- **{TOTAL_CLUSTERS}** unique clusters
- **{REAL_COUNT} real** + {SMELL_COUNT} smell + {NIT_COUNT} nit + {WRONG_COUNT} wrong ({HALLUCINATION_PCT}% hallucinations)
- **{MODELS_WITH_REAL}/{TOTAL_MODELS}** models found at least one real bug

## Real bugs

{REAL_BUGS_TABLE}

## Who found what

{PER_MODEL_REAL_TABLE}

## Singleton findings (only one model)

{SINGLETON_FINDINGS_LIST}

<!-- TODO: for each singleton above, explain in 1-2 lines why other models missed it.
     What's non-obvious about the bug? Could static analysis catch it?
     What did the lone model key on that the others didn't? -->

## Severity calibration — model labels vs ground truth

{SEVERITY_CALIBRATION_TABLE}

<!-- TODO: one sentence of interpretation — does model severity correlate with
     real importance? If not, that's a finding worth highlighting. -->

## Cost / value

{COST_VALUE_SUMMARY}

## Patterns

### What models found well

<!-- TODO: bullet list. Bug categories that most models caught. Examples:
     duplication, null-safety, obvious LINQ inefficiency, by-design quirks. -->

### What models found poorly

<!-- TODO: bullet list. Bug categories that only 1-2 models caught.
     What did the diff hide that pattern-matching couldn't see? -->

### What models hallucinated

<!-- TODO: bullet list. Categories of false positives (`wrong` verdicts).
     Are there common shapes? E.g. "claims N+1 problem on fixed-shape queries",
     "flags missing using without checking ImplicitUsings flag". -->

## Methodology takeaway

<!-- TODO (2-3 sentences): the single most useful insight from this run.
     This is the part future readers will quote. Examples from past runs:
     - "AI review and test coverage are complementary, not substitutes —
        most real bugs lived in handler logic, where snapshot tests don't reach."
     - "Model-assigned severity does not correlate with real impact;
        cannot be used for triage without a second human pass."
     - "Only one model found bug #X — useful as ensemble signal, not as a
        recommendation to switch to that model." -->
