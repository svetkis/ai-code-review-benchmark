#!/usr/bin/env python3
"""
aggregate_findings.py — parse code-review findings and render the worklist.

No LLM calls inside. Clustering is a separate step performed by Claude in
the chat session, or via `llm_judge.py cluster`.

Usage:
  # 1. Parse all .md → findings.json
  python aggregate_findings.py parse \
      --results-dir results \
      --output findings.json

  # 2. (separately) Cluster the findings (Claude in chat or
  #    `llm_judge.py cluster`); result is clusters.json with structure
  #    {"clusters": [{"id": N, "topic": "...",
  #    "consensus_severity": "...", "members": [<int idx>]}]}

  # 3. Render the worklist
  python aggregate_findings.py render \
      --findings findings.json \
      --clusters clusters.json \
      --output worklist.md
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


# ── Format compliance ──────────────────────────────────────────────────────

FORMAT_NOTES = {}


# ── Findings parser ────────────────────────────────────────────────────────

ISSUE_RE = re.compile(
    r'^[\s\*\_]*(?:#+\s+)?[\s\*\_]*(\d+)[\)\.]\s*\*{0,2}\s*'
    r'\[(?:severity:\s*)?([^\]]+)\]\s*\*{0,2}[ \t]*(.*)$',
    re.MULTILINE,
)

FIELDS = ["Location", "Why it matters", "Evidence", "Recommendation"]


def _strip_md_decor(line: str) -> str:
    s = re.sub(r'^[\s\-\*]+', '', line)
    return s.replace('**', '')


def _extract(body: str, field: str) -> str:
    lines = body.split('\n')
    other_fields = [f for f in FIELDS if f != field]
    result = []
    capturing = False
    for line in lines:
        stripped = _strip_md_decor(line)
        if stripped.startswith(field + ':') or stripped.startswith(field + ' :'):
            capturing = True
            content = stripped.split(':', 1)[1].lstrip()
            if content:
                result.append(content)
            continue
        if capturing:
            stripped2 = _strip_md_decor(line)
            if any(stripped2.startswith(o + ':') for o in other_fields):
                break
            result.append(line)
    return '\n'.join(result).strip()


def parse_md(path: Path) -> list[dict]:
    text = path.read_text(encoding='utf-8')
    matches = list(ISSUE_RE.finditer(text))
    issues = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        issues.append({
            "model": path.stem,
            "severity": m.group(2).strip().lower(),
            "summary": m.group(3).strip(),
            "location": _extract(body, "Location"),
            "why_it_matters": _extract(body, "Why it matters"),
            "evidence": _extract(body, "Evidence"),
            "recommendation": _extract(body, "Recommendation"),
        })
    return issues


def collect_issues(*dirs: Path) -> list[dict]:
    issues = []
    for d in dirs:
        if not d or not d.exists():
            continue
        for md in sorted(d.glob("*.md")):
            try:
                parsed = parse_md(md)
                issues.extend(parsed)
                print(f"  {md.name}: {len(parsed)} findings")
            except Exception as e:
                print(f"  ! {md.name}: {e}")
    return issues


# ── Worklist renderer ──────────────────────────────────────────────────────

SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}


def _dedent_block(text: str) -> str:
    lines = text.split('\n')
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return text
    common = min(len(l) - len(l.lstrip()) for l in non_empty)
    if common == 0:
        return text
    return '\n'.join(l[common:] if len(l) >= common else l for l in lines)


def render(clusters: list[dict], issues: list[dict], output: Path) -> None:
    clusters.sort(key=lambda c: (SEV_ORDER.get(c["consensus_severity"], 99),
                                  -len(c["members"])))

    all_models = sorted(set(i['model'] for i in issues))
    models_total = len(all_models)
    violators = [m for m in all_models if m in FORMAT_NOTES]

    out = [
        "# Code Review Worklist",
        "",
        f"Clusters: **{len(clusters)}** · Total findings: **{len(issues)}** · Models: **{models_total}**",
        "",
        "## How to label",
        "",
        "For each cluster pick one label (mark `[x]`):",
        "- **real** — genuine issue, fix it",
        "- **smell** — code smell (refactoring), won't break in production",
        "- **nit** — pure style / optional",
        "- **wrong** — model is mistaken / hallucinated / not applicable",
        "",
    ]

    if violators:
        out.append("## Format compliance")
        out.append("")
        out.append("These models did not follow the response format strictly (parsed, but with caveats):")
        out.append("")
        for m in violators:
            out.append(f"- **{m}** — {FORMAT_NOTES[m]}")
        out.append("")

    out.append("---")

    for c in clusters:
        members = [issues[i] for i in c["members"]]
        models = sorted(set(m["model"] for m in members))
        sev_counts = Counter(m['severity'] for m in members)
        sev_str = ", ".join(f"{k}×{v}" for k, v in sorted(sev_counts.items(),
                                                           key=lambda x: SEV_ORDER.get(x[0], 99)))

        location = max((m.get('location') or '' for m in members), key=len, default="")
        evidence = max((m.get('evidence') or '' for m in members), key=len, default="")

        out.append(f"\n## #{c['id']}: {c['topic']}\n")
        out.append(f"**Severity:** {c['consensus_severity']} ({sev_str})  ")
        out.append(f"**Found by ({len(models)}/{models_total}):** {', '.join(models)}  ")
        if location:
            location_short = location[:250].replace('\n', ' ').replace('`', "'")
            out.append(f"**Location:** `{location_short}`")
        out.append("")

        if evidence:
            ev_short = _dedent_block(evidence[:800].rstrip())
            out.append("<details><summary>Evidence (best quote)</summary>")
            out.append("")
            out.append(ev_short)
            out.append("")
            out.append("</details>")
            out.append("")

        out.append("**Verdict:**")
        out.append("- [ ] real")
        out.append("- [ ] smell")
        out.append("- [ ] nit")
        out.append("- [ ] wrong")
        out.append("")
        out.append("**Note:** _____")
        out.append("")

        out.append(f"<details><summary>All quotes ({len(members)})</summary>")
        out.append("")
        for m in members:
            summary = m['summary'][:250].replace('\n', ' ')
            badge = " ⚠" if m['model'] in FORMAT_NOTES else ""
            out.append(f"- **{m['model']}**{badge} [{m['severity']}]: {summary}")
        out.append("")
        out.append("</details>")
        out.append("")

    output.write_text("\n".join(out), encoding='utf-8')


# ── CLI ────────────────────────────────────────────────────────────────────


def cmd_parse(args) -> None:
    print("=== Collecting findings ===")
    issues = collect_issues(args.results_dir)
    models = sorted(set(i['model'] for i in issues))
    print(f"\nTotal: {len(issues)} findings from {len(models)} models")
    args.output.write_text(
        json.dumps({"issues": issues}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(f"Saved: {args.output}")
    print(
        "\nNext: cluster the findings (Claude in chat or `llm_judge.py "
        'cluster`); save as clusters.json with structure {"clusters": [...]} '
        "and run `aggregate_findings.py render`."
    )


def cmd_render(args) -> None:
    findings = json.loads(args.findings.read_text(encoding='utf-8'))
    clusters_data = json.loads(args.clusters.read_text(encoding='utf-8'))
    issues = findings["issues"]
    clusters = clusters_data["clusters"]
    print(f"Issues: {len(issues)} · Clusters: {len(clusters)}")
    render(clusters, issues, args.output)
    print(f"Worklist: {args.output}")


def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    p = argparse.ArgumentParser(description="Code-review findings parser (no LLM).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="Parse .md reviews into findings.json")
    pp.add_argument("--results-dir", type=Path, default=Path("results"))
    pp.add_argument("--output", "-o", type=Path, default=Path("findings.json"))
    pp.set_defaults(func=cmd_parse)

    pr = sub.add_parser("render", help="Render worklist.md from findings + clusters")
    pr.add_argument("--findings", type=Path, default=Path("findings.json"))
    pr.add_argument("--clusters", type=Path, default=Path("clusters.json"))
    pr.add_argument("--output", "-o", type=Path, default=Path("worklist.md"))
    pr.set_defaults(func=cmd_render)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
