#!/usr/bin/env python3
"""
compute_metrics.py — compute per-model metrics from adjudicated verdicts.

Usage:
  python compute_metrics.py
      [--verdicts verdicts.md]
      [--clusters clusters.json]
      [--findings findings.json]
      [--results results.json]
      [--worklist worklist.md]
      [--merge-into worklist_judged.md]
      [--leaderboard leaderboard.md]

What it does:
  1. Parses verdicts.md (verdict + reason + confidence per cluster).
  2. Optionally merges into worklist.md (checkboxes + judge notes).
  3. Computes per-model metrics: precision, recall, hallucination rate, $/real.
  4. Renders leaderboard.md.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ── Verdicts parser ────────────────────────────────────────────────────────

VERDICT_BLOCK_RE = re.compile(
    r'^##\s*Cluster\s+(\d+)\s*\n'
    r'(?:.*?)'
    r'-\s*Verdict:\s*(real|smell|nit|wrong)\s*\n'
    r'(?:.*?)'
    r'(?:-\s*Confidence:\s*(high|medium|low)\s*\n)?'
    r'(?:.*?)'
    r'-\s*Reason:\s*(.+?)(?=\n##|\n\n##|\Z)',
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def parse_verdicts(path: Path) -> dict[int, dict]:
    text = path.read_text(encoding='utf-8')
    verdicts = {}
    # Split on `## Cluster N` and parse each block independently — more robust.
    blocks = re.split(r'(?=^##\s*Cluster\s+\d+)', text, flags=re.MULTILINE)
    for block in blocks:
        m_id = re.match(r'^##\s*Cluster\s+(\d+)', block)
        if not m_id:
            continue
        cluster_id = int(m_id.group(1))
        m_v = re.search(r'-\s*Verdict:\s*(real|smell|nit|wrong)', block, re.IGNORECASE)
        if not m_v:
            continue
        m_c = re.search(r'-\s*Confidence:\s*(high|medium|low)', block, re.IGNORECASE)
        m_r = re.search(r'-\s*Reason:\s*(.+?)(?=\n-|\n##|\Z)', block, re.IGNORECASE | re.DOTALL)
        verdicts[cluster_id] = {
            "verdict": m_v.group(1).lower(),
            "confidence": m_c.group(1).lower() if m_c else "medium",
            "reason": (m_r.group(1).strip() if m_r else "").replace('\n', ' '),
        }
    return verdicts


# ── Metrics ────────────────────────────────────────────────────────────────

CATEGORIES = ["real", "smell", "nit", "wrong"]


def compute_per_model(clusters: list[dict], issues: list[dict],
                      verdicts: dict[int, dict]) -> dict[str, dict]:
    """For each model: found_<cat>, missed_real, missed_smell, total_findings."""
    all_models = sorted(set(i['model'] for i in issues))
    stats = {m: {f"found_{c}": 0 for c in CATEGORIES} for m in all_models}
    for m in all_models:
        stats[m]["missed_real"] = 0
        stats[m]["missed_smell"] = 0
        stats[m]["total_findings"] = sum(1 for i in issues if i['model'] == m)

    for cluster in clusters:
        cid = cluster["id"]
        v = verdicts.get(cid)
        if not v:
            continue  # skip unadjudicated clusters
        cat = v["verdict"]
        cluster_models = set(issues[i]['model'] for i in cluster["members"])
        for m in all_models:
            if m in cluster_models:
                stats[m][f"found_{cat}"] += 1
            elif cat in ("real", "smell"):
                stats[m][f"missed_{cat}"] += 1
    return stats


def derive_metrics(stats: dict[str, dict]) -> dict[str, dict]:
    """Precision, recall, hallucination rate."""
    out = {}
    for model, s in stats.items():
        signal = s["found_real"] + s["found_smell"] + s["found_nit"]
        total_marked = signal + s["found_wrong"]
        precision = signal / total_marked if total_marked else 0.0
        precision_strict = (
            (s["found_real"] + s["found_smell"]) / total_marked
            if total_marked else 0.0
        )
        total_real = s["found_real"] + s["missed_real"]
        recall_real = s["found_real"] / total_real if total_real else 0.0
        total_smell = s["found_smell"] + s["missed_smell"]
        recall_smell = s["found_smell"] / total_smell if total_smell else 0.0
        hallucination_rate = (
            s["found_wrong"] / s["total_findings"]
            if s["total_findings"] else 0.0
        )
        out[model] = {
            **s,
            "precision_strict": precision_strict,  # real+smell / marked
            "precision_loose": precision,           # real+smell+nit / marked
            "recall_real": recall_real,
            "recall_smell": recall_smell,
            "hallucination_rate": hallucination_rate,
            "signal": signal,
        }
    return out


# ── Merge into worklist ────────────────────────────────────────────────────

CLUSTER_HEADER_RE = re.compile(r'^##\s+#(\d+):\s*(.+)$', re.MULTILINE)


def merge_into_worklist(worklist_path: Path, verdicts: dict[int, dict],
                         output_path: Path) -> None:
    text = worklist_path.read_text(encoding='utf-8')
    headers = list(CLUSTER_HEADER_RE.finditer(text))
    chunks = [text[: headers[0].start()] if headers else text]
    for i, m in enumerate(headers):
        cid = int(m.group(1))
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[m.start():end]
        v = verdicts.get(cid)
        if v:
            section = _apply_verdict(section, v)
        chunks.append(section)
    output_path.write_text("".join(chunks), encoding='utf-8')


def _apply_verdict(section: str, v: dict) -> str:
    """Mark `[x]` next to the chosen category and write the Note line."""
    verdict = v["verdict"]
    for cat in CATEGORIES:
        marker = "[x]" if cat == verdict else "[ ]"
        section = re.sub(rf'^-\s*\[[ x]\]\s*{cat}$',
                         f'- {marker} {cat}',
                         section,
                         count=1,
                         flags=re.MULTILINE)
    note = f"_(Judge {v['confidence']}): {v['reason']}_"
    section = re.sub(r'^\*\*Note:\*\*\s*_____$',
                     f'**Note:** {note}',
                     section,
                     count=1,
                     flags=re.MULTILINE)
    return section


# ── Leaderboard ────────────────────────────────────────────────────────────

def render_leaderboard(metrics: dict[str, dict], cost_data: dict[str, dict],
                        output: Path, total_real: int, total_smell: int) -> None:
    rows = sorted(metrics.items(),
                  key=lambda kv: (-kv[1]["recall_real"],
                                   kv[1]["hallucination_rate"]))
    out = []
    out.append("# Code Review Benchmark — Leaderboard\n")
    out.append(f"**Real**: {total_real} · **Smell**: {total_smell} · "
               f"Models: **{len(metrics)}**\n")
    out.append("Metrics:")
    out.append("- **Recall (real)** — % of real bugs found by the model")
    out.append("- **Precision** — % of its findings that were real/smell (excluding nit and wrong)")
    out.append("- **Halluc.** — % of its findings labelled wrong")
    out.append("- **$/real** — cost per real bug found (`*` — estimate)\n")

    out.append("| Model | Real | Smell | Nit | Wrong | Recall | Precision | Halluc. | $ | $/real |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for model, m in rows:
        cost_info = cost_data.get(model)
        if cost_info:
            cost = cost_info["usd"]
            mark = "" if cost_info["kind"] == "actual" else "*"
            cost_str = f"${cost:.2f}{mark}"
            cost_per_real = (
                f"${cost / m['found_real']:.2f}{mark}"
                if m['found_real'] > 0 else (f"∞{mark}" if cost > 0 else "-")
            )
        else:
            cost_str = "-"
            cost_per_real = "-"
        out.append(
            f"| {model} | {m['found_real']} | {m['found_smell']} | "
            f"{m['found_nit']} | {m['found_wrong']} | "
            f"{m['recall_real']*100:.0f}% | {m['precision_strict']*100:.0f}% | "
            f"{m['hallucination_rate']*100:.0f}% | {cost_str} | {cost_per_real} |"
        )

    out.append("")
    out.append(
        "`*` — estimate based on token usage × public rates "
        "(Anthropic for Claude, OpenRouter for open-weight models not exposed in the dashboard)."
    )
    out.append("`∞` — model found zero real bugs → $/real is infinite.")

    output.write_text("\n".join(out), encoding='utf-8')


# ── Findings report ────────────────────────────────────────────────────────

DEFAULT_REPORT_TEMPLATE = (
    Path(__file__).parent / "templates" / "findings_report.template.md"
)


def _location_short(s: str | None, limit: int = 80) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ").replace("`", "'")
    return s[:limit] + ("..." if len(s) > limit else "")


def _real_bugs_table(clusters: list[dict], issues: list[dict],
                      verdicts: dict[int, dict], models_total: int) -> str:
    rows = []
    for c in clusters:
        v = verdicts.get(c["id"])
        if not v or v["verdict"] != "real":
            continue
        members = [issues[i] for i in c["members"] if 0 <= i < len(issues)]
        coverage = len({m["model"] for m in members})
        location = max((m.get("location") or "" for m in members), key=len, default="")
        rows.append((c["id"], c["topic"], _location_short(location),
                     c.get("consensus_severity", "?"),
                     f"{coverage}/{models_total}"))
    if not rows:
        return "_No real bugs in this run._"
    out = ["| # | Topic | Location | Severity (consensus) | Coverage |",
           "|---|---|---|---|---:|"]
    for cid, topic, loc, sev, cov in rows:
        out.append(f"| **#{cid}** | {topic} | `{loc}` | {sev} | {cov} |")
    return "\n".join(out)


def _per_model_real_table(clusters: list[dict], issues: list[dict],
                            verdicts: dict[int, dict]) -> str:
    real_clusters = [c for c in clusters
                     if verdicts.get(c["id"], {}).get("verdict") == "real"]
    if not real_clusters:
        return "_No real bugs to attribute._"
    real_clusters.sort(key=lambda c: c["id"])

    all_models = sorted({i["model"] for i in issues})
    cluster_models = {c["id"]: {issues[i]["model"] for i in c["members"]
                                  if 0 <= i < len(issues)}
                       for c in real_clusters}

    header = "| Model | " + " | ".join(f"#{c['id']}" for c in real_clusters) + " | Total real |"
    sep = "|---|" + ":-:|" * len(real_clusters) + "---:|"
    body = []
    for m in all_models:
        cells = ["✓" if m in cluster_models[c["id"]] else "" for c in real_clusters]
        total = sum(1 for c in real_clusters if m in cluster_models[c["id"]])
        body.append((total, f"| {m} | " + " | ".join(cells) + f" | {total} |"))
    body.sort(key=lambda x: -x[0])
    return "\n".join([header, sep, *(row for _, row in body)])


def _singleton_findings_list(clusters: list[dict], issues: list[dict],
                               verdicts: dict[int, dict]) -> str:
    rows = []
    for c in clusters:
        members = [issues[i] for i in c["members"] if 0 <= i < len(issues)]
        unique_models = {m["model"] for m in members}
        if len(unique_models) != 1:
            continue
        model = next(iter(unique_models))
        v = verdicts.get(c["id"], {})
        verdict = v.get("verdict", "?")
        rows.append(f"- **#{c['id']}** — flagged only by **{model}** "
                     f"(verdict: _{verdict}_): {c['topic']}")
    if not rows:
        return "_No singleton clusters in this run._"
    return "\n".join(rows)


def _severity_calibration_table(clusters: list[dict],
                                  verdicts: dict[int, dict]) -> str:
    severities = ["blocker", "major", "minor", "nit"]
    cats = ["real", "smell", "nit", "wrong"]
    grid = {s: {c: 0 for c in cats} for s in severities}
    for c in clusters:
        v = verdicts.get(c["id"])
        if not v:
            continue
        sev = (c.get("consensus_severity") or "").lower()
        if sev not in grid:
            continue
        grid[sev][v["verdict"]] += 1
    out = ["| Cluster severity | → real | → smell | → nit | → wrong | Total |",
           "|---|---:|---:|---:|---:|---:|"]
    for s in severities:
        row = grid[s]
        total = sum(row.values())
        if total == 0:
            continue
        out.append(f"| {s} | {row['real']} | {row['smell']} | {row['nit']} | "
                    f"{row['wrong']} | {total} |")
    if len(out) == 2:
        return "_No adjudicated clusters to calibrate._"
    return "\n".join(out)


def _cost_value_summary(metrics: dict[str, dict],
                          cost_data: dict[str, dict]) -> str:
    if not metrics:
        return "_No models to compare._"
    by_recall = sorted(metrics.items(), key=lambda kv: -kv[1]["recall_real"])
    by_halluc = sorted(metrics.items(), key=lambda kv: -kv[1]["hallucination_rate"])
    by_precision = sorted(metrics.items(), key=lambda kv: -kv[1]["precision_strict"])

    cost_per_real = []
    for model, m in metrics.items():
        if m["found_real"] <= 0:
            continue
        info = cost_data.get(model)
        if not info or info.get("usd") is None:
            continue
        cost_per_real.append((model, info["usd"] / m["found_real"], info))
    cost_per_real.sort(key=lambda x: x[1])

    out = []
    top_recall = by_recall[0]
    out.append(f"- **Best recall (real):** {top_recall[0]} — "
                f"{int(top_recall[1]['recall_real'] * 100)}% "
                f"({top_recall[1]['found_real']}/{top_recall[1]['found_real'] + top_recall[1]['missed_real']})")
    if cost_per_real:
        m, cpr, info = cost_per_real[0]
        mark = "" if info.get("kind") == "actual" else "*"
        out.append(f"- **Best $/real:** {m} — ${cpr:.2f}{mark}")
    top_precision = by_precision[0]
    out.append(f"- **Best precision (real+smell):** {top_precision[0]} — "
                f"{int(top_precision[1]['precision_strict'] * 100)}%")
    worst_halluc = by_halluc[0]
    if worst_halluc[1]["hallucination_rate"] > 0:
        out.append(f"- **Highest hallucination rate:** {worst_halluc[0]} — "
                    f"{int(worst_halluc[1]['hallucination_rate'] * 100)}% "
                    f"of its findings labelled wrong")
    return "\n".join(out)


def render_findings_report(
    template_path: Path,
    output_path: Path,
    run_id: str,
    clusters: list[dict],
    issues: list[dict],
    verdicts: dict[int, dict],
    metrics: dict[str, dict],
    cost_data: dict[str, dict],
) -> None:
    by_cat = Counter(v["verdict"] for v in verdicts.values())
    all_models = sorted({i["model"] for i in issues})
    models_with_real = sum(1 for m in all_models
                            if metrics.get(m, {}).get("found_real", 0) > 0)
    halluc_pct = (
        round(by_cat.get("wrong", 0) * 100 / len(clusters), 1)
        if clusters else 0.0
    )

    template = template_path.read_text(encoding="utf-8")
    substitutions = {
        "{RUN_ID}": run_id,
        "{TOTAL_FINDINGS}": str(len(issues)),
        "{TOTAL_MODELS}": str(len(all_models)),
        "{TOTAL_CLUSTERS}": str(len(clusters)),
        "{REAL_COUNT}": str(by_cat.get("real", 0)),
        "{SMELL_COUNT}": str(by_cat.get("smell", 0)),
        "{NIT_COUNT}": str(by_cat.get("nit", 0)),
        "{WRONG_COUNT}": str(by_cat.get("wrong", 0)),
        "{HALLUCINATION_PCT}": f"{halluc_pct}",
        "{MODELS_WITH_REAL}": str(models_with_real),
        "{REAL_BUGS_TABLE}": _real_bugs_table(clusters, issues, verdicts, len(all_models)),
        "{PER_MODEL_REAL_TABLE}": _per_model_real_table(clusters, issues, verdicts),
        "{SINGLETON_FINDINGS_LIST}": _singleton_findings_list(clusters, issues, verdicts),
        "{SEVERITY_CALIBRATION_TABLE}": _severity_calibration_table(clusters, verdicts),
        "{COST_VALUE_SUMMARY}": _cost_value_summary(metrics, cost_data),
    }
    out = template
    for key, value in substitutions.items():
        out = out.replace(key, value)
    output_path.write_text(out, encoding="utf-8")


# ── Cost loader ────────────────────────────────────────────────────────────

def load_cost_data(cost_estimates: Path, results_json: Path) -> dict[str, dict]:
    """Return {model_name: {'usd': float, 'kind': str, 'source': str}}.

    Priority: cost_estimates.json (explicit annotation) → results.json
    (if cost is present in the API response).
    """
    out: dict[str, dict] = {}
    if cost_estimates and cost_estimates.exists():
        data = json.loads(cost_estimates.read_text(encoding='utf-8'))
        for k, v in data.items():
            if k.startswith("_"):
                continue
            out[k] = v
    if results_json and results_json.exists():
        data = json.loads(results_json.read_text(encoding='utf-8'))
        for name, res in data.get("results", {}).items():
            key = name.replace(" ", "_").replace(".", "_")
            if key in out:
                continue
            cost = res.get("cost") or (res.get("usage", {}) or {}).get("cost")
            if cost is not None:
                out[key] = {"usd": float(cost), "kind": "actual",
                            "source": "results.json/api"}
    return out


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    p = argparse.ArgumentParser(description="Compute metrics from adjudicated verdicts.")
    p.add_argument("--verdicts", type=Path, default=Path("verdicts.md"))
    p.add_argument("--findings", type=Path, default=Path("findings.json"),
                   help="findings.json produced by `aggregate_findings.py parse`")
    p.add_argument("--clusters", type=Path, default=Path("clusters.json"),
                   help="clusters.json produced by Claude clustering step")
    p.add_argument("--results", type=Path, default=Path("results.json"),
                   help="results.json from code_review_benchmark.py (for API cost)")
    p.add_argument("--cost-estimates", type=Path, default=Path("cost_estimates.json"),
                   help="Manual cost annotations (takes priority over API cost).")
    p.add_argument("--worklist", type=Path, default=Path("worklist.md"),
                   help="Path to the source worklist.md (for merge).")
    p.add_argument("--merge-into", type=Path, default=Path("worklist_judged.md"),
                   help="Where to save the worklist with merged verdicts.")
    p.add_argument("--leaderboard", type=Path, default=Path("leaderboard.md"))
    p.add_argument("--report", type=Path, default=None,
                   help="Optional: render findings_report.md (skeleton with auto-filled "
                         "tables and `<!-- TODO -->` blocks for human prose).")
    p.add_argument("--report-template", type=Path, default=DEFAULT_REPORT_TEMPLATE,
                   help="Template for --report (default: templates/findings_report.template.md)")
    p.add_argument("--run-id", type=str, default=None,
                   help="Run id for the report title; defaults to verdicts.md parent dir name.")
    args = p.parse_args()

    if not args.verdicts.exists():
        print(f"Verdicts file not found: {args.verdicts}"); sys.exit(1)
    if not args.clusters.exists():
        print(f"Clusters file not found: {args.clusters}"); sys.exit(1)

    print(f"Parsing verdicts from {args.verdicts}")
    verdicts = parse_verdicts(args.verdicts)
    print(f"  Adjudicated: {len(verdicts)} clusters")
    by_cat = Counter(v["verdict"] for v in verdicts.values())
    by_conf = Counter(v["confidence"] for v in verdicts.values())
    print(f"  By category: {dict(by_cat)}")
    print(f"  By confidence: {dict(by_conf)}")

    clusters_data = json.loads(args.clusters.read_text(encoding='utf-8'))
    clusters = clusters_data["clusters"]
    # Issues may live inside clusters.json (legacy combined format) or in a
    # separate findings.json.
    if "issues" in clusters_data:
        issues = clusters_data["issues"]
    elif args.findings.exists():
        issues = json.loads(args.findings.read_text(encoding='utf-8'))["issues"]
    else:
        print(f"Issues not found in either {args.clusters} or {args.findings}"); sys.exit(1)

    print("\nMerging verdicts into worklist")
    if args.worklist.exists():
        merge_into_worklist(args.worklist, verdicts, args.merge_into)
        print(f"  Saved: {args.merge_into}")
    else:
        print(f"  Worklist {args.worklist} not found — skipping merge")

    print("\nComputing metrics")
    stats = compute_per_model(clusters, issues, verdicts)
    metrics = derive_metrics(stats)

    cost_data = load_cost_data(args.cost_estimates, args.results)
    print(f"  Cost data: {len(cost_data)} models")

    total_real = by_cat.get("real", 0)
    total_smell = by_cat.get("smell", 0)
    render_leaderboard(metrics, cost_data, args.leaderboard, total_real, total_smell)
    print(f"  Leaderboard: {args.leaderboard}")

    if args.report:
        if not args.report_template.is_file():
            print(f"  ! Report template not found: {args.report_template}")
        else:
            run_id = args.run_id or args.verdicts.resolve().parent.name
            render_findings_report(
                template_path=args.report_template,
                output_path=args.report,
                run_id=run_id,
                clusters=clusters,
                issues=issues,
                verdicts=verdicts,
                metrics=metrics,
                cost_data=cost_data,
            )
            print(f"  Findings report (skeleton): {args.report}")


if __name__ == "__main__":
    main()
