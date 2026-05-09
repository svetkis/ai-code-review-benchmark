#!/usr/bin/env python3
"""
llm_judge.py — Optional automated draft for the two LLM-driven steps.

The default workflow uses Claude in the chat session for clustering (step 3)
and adjudication (step 5). This script exists for users without Claude Code,
or for reproducibility studies (running multiple judge models and comparing).

The output of `adjudicate` is `verdicts.draft.md` — NOT `verdicts.md`. A
human reviewer is still expected to read the draft, override calls they
disagree with, and save the result as `verdicts.md` before running
`compute_metrics.py`. The draft includes a "Needs human attention" preamble
that flags low-confidence, severity-dissent, and singleton clusters first.

Usage:
  # Step 3 alt — automated clustering
  python llm_judge.py cluster \\
      --findings findings.json \\
      -o clusters.json \\
      --judge-model openai/gpt-5.5

  # Step 5 alt — automated adjudication draft
  python llm_judge.py adjudicate \\
      --clusters clusters.json \\
      --findings findings.json \\
      --repo-path /path/to/repo \\
      --context-lines 50 \\
      -o verdicts.draft.md \\
      --judge-model openai/gpt-5.5

Environment:
  OPENROUTER_API_KEY  — required
  OPENROUTER_REFERER  — optional HTTP-Referer override
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing 'requests' package: pip install requests")
    sys.exit(1)


# ── OpenRouter call ─────────────────────────────────────────────────────────

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_REFERER = "https://github.com/svetkis/ai-code-review-benchmark"


def call_openrouter(
    model_id: str,
    prompt: str,
    api_key: str,
    timeout: int = 600,
    max_tokens: int = 16000,
) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", DEFAULT_REFERER),
        "X-Title": "Code Review Benchmark — LLM Judge",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    t0 = time.time()
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
        elapsed = round(time.time() - t0, 2)
        if resp.status_code != 200:
            return {"status": "error", "http_code": resp.status_code,
                    "error": resp.text[:1000], "elapsed_sec": elapsed}
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"status": "ok", "content": content, "elapsed_sec": elapsed}
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "timeout", "elapsed_sec": timeout}
    except Exception as e:
        return {"status": "error", "error": str(e),
                "elapsed_sec": round(time.time() - t0, 2)}


# ── Severity helpers ────────────────────────────────────────────────────────

SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}


def sev_index(sev: str | None) -> int | None:
    if not sev:
        return None
    s = sev.strip().lower()
    for k, v in SEV_ORDER.items():
        if k in s:
            return v
    return None


# ── Step 3: clustering ──────────────────────────────────────────────────────

def render_findings_block(issues: list[dict]) -> str:
    """Compact text rendering of findings, indexed for the model to reference."""
    lines = []
    for i, it in enumerate(issues):
        lines.append(f"[{i}] model={it.get('model','?')} severity={it.get('severity','?')}")
        for field, key in (("summary", "summary"), ("location", "location"),
                            ("why", "why_it_matters"), ("rec", "recommendation")):
            val = (it.get(key) or "").strip().replace("\n", " ")
            if val:
                lines.append(f"    {field}: {val[:400]}")
        lines.append("")
    return "\n".join(lines)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of an LLM response (with or without fence)."""
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return json.loads(m.group(1))
    # Fallback: locate the first '{' and parse from there.
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in response")
    return json.loads(text[start:])


def cmd_cluster(args) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: set OPENROUTER_API_KEY"); sys.exit(1)

    findings = json.loads(args.findings.read_text(encoding="utf-8"))
    issues = findings["issues"]
    print(f"Findings to cluster: {len(issues)}")

    template = args.prompt.read_text(encoding="utf-8")
    prompt = template.replace("{findings_block}", render_findings_block(issues))

    print(f"Calling judge: {args.judge_model}")
    res = call_openrouter(args.judge_model, prompt, api_key,
                          timeout=args.timeout, max_tokens=args.max_tokens)
    if res["status"] != "ok":
        print(f"Error: {res.get('error') or res.get('http_code')}")
        sys.exit(2)
    print(f"  Response in {res['elapsed_sec']}s")

    try:
        parsed = extract_json_object(res["content"])
    except Exception as e:
        debug_path = args.output.with_suffix(".raw.txt")
        debug_path.write_text(res["content"], encoding="utf-8")
        print(f"Error: response is not valid JSON ({e}). Raw response: {debug_path}")
        sys.exit(2)

    if "clusters" not in parsed:
        print("Error: response JSON has no 'clusters' key")
        sys.exit(2)

    clusters = parsed["clusters"]
    used = {idx for c in clusters for idx in c.get("members", [])}
    missing = set(range(len(issues))) - used
    if missing:
        print(f"Warning: {len(missing)} findings not assigned to any cluster: "
              f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}")

    args.output.write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Clusters: {len(clusters)} → {args.output}")


# ── Step 5: source excerpt ──────────────────────────────────────────────────

LOCATION_FILE_LINE_RE = re.compile(r"([\w./\\\-]+\.\w{1,5}):(\d+)")


def parse_location(location: str | None) -> tuple[str, int] | None:
    """Return (filename, line) from a location string, or None if unparseable.

    Locations in the wild look like:
        `RmrHeadcountCalculator.cs:30-40`
        LoadPlansByAveFiltersHandler.Handle:158
        `PlanHeadcountAveCalculator.cs:30-35` vs `PlanHeadcountAveCalculator.cs:55-60`
        PlanHeadcountIndicatorsCalculator, PlanHeadcountAveCalculator.

    We require an extension to be confident we have a real file reference;
    `Method:158` (no extension) returns None.
    """
    if not location:
        return None
    m = LOCATION_FILE_LINE_RE.search(location)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def find_source_file(repo_path: Path, filename: str) -> Path | None:
    """Resolve a filename inside repo_path; prefer exact relative path match."""
    candidate = repo_path / filename
    if candidate.is_file():
        return candidate
    basename = Path(filename).name
    matches = list(repo_path.rglob(basename))
    if not matches:
        return None
    if len(matches) > 1:
        path_lower = filename.lower().replace("\\", "/")
        for m in matches:
            if str(m).lower().replace("\\", "/").endswith(path_lower):
                return m
    return matches[0]


def build_source_excerpt(
    repo_path: Path | None,
    location: str | None,
    context_lines: int,
    full_file_threshold: int = 200,
) -> tuple[str, str]:
    """Return (excerpt_text, status). status ∈ {available, unavailable}."""
    if repo_path is None:
        return "(--repo-path not provided; judging without source)", "unavailable"
    parsed = parse_location(location)
    if not parsed:
        return f"(unable to parse file:line from location: {location!r})", "unavailable"
    filename, line = parsed
    src_path = find_source_file(repo_path, filename)
    if not src_path:
        return f"(file not found in repo-path: {filename})", "unavailable"
    try:
        text = src_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"(could not read {src_path}: {e})", "unavailable"
    lines = text.splitlines()
    total = len(lines)
    if total <= full_file_threshold:
        body = "\n".join(f"{i+1:>4}: {ln}" for i, ln in enumerate(lines))
        return f"--- {src_path} (full file, {total} lines) ---\n{body}", "available"
    start = max(1, line - context_lines)
    end = min(total, line + context_lines)
    body = "\n".join(f"{i:>4}: {lines[i-1]}" for i in range(start, end + 1))
    return f"--- {src_path}:{start}-{end} (cited line: {line}) ---\n{body}", "available"


# ── Step 5: per-cluster adjudication ────────────────────────────────────────

def render_cluster_findings(members: list[dict]) -> str:
    out = []
    for m in members:
        sev = m.get("severity") or "?"
        model = m.get("model") or "?"
        summary = (m.get("summary") or "").replace("\n", " ").strip()
        location = (m.get("location") or "").replace("\n", " ").strip()
        why = (m.get("why_it_matters") or "").replace("\n", " ").strip()
        out.append(f"- [{sev}] {model}: {summary}")
        if location:
            out.append(f"    location: {location[:300]}")
        if why:
            out.append(f"    why: {why[:400]}")
    return "\n".join(out)


def pick_best_location(members: list[dict]) -> str | None:
    """Pick the longest non-empty location across cluster members (matches the
    convention in aggregate_findings.render — most-specific tends to be longest)."""
    locations = [m.get("location") or "" for m in members]
    locations = [l for l in locations if l.strip()]
    if not locations:
        return None
    return max(locations, key=len)


def attention_flags(cluster: dict, members: list[dict], confidence: str) -> list[str]:
    """Reasons this cluster needs human attention. Empty list means none."""
    reasons = []
    if confidence == "low":
        reasons.append("low confidence")
    severities = [sev_index(m.get("severity")) for m in members]
    severities = [s for s in severities if s is not None]
    if severities and (max(severities) - min(severities)) >= 2:
        # Map back to names for the human-readable preamble.
        sev_name = {v: k for k, v in SEV_ORDER.items()}
        reasons.append(f"severity dissent ({sev_name[min(severities)]}↔{sev_name[max(severities)]})")
    unique_models = {m.get("model") for m in members if m.get("model")}
    if len(unique_models) == 1:
        only = next(iter(unique_models))
        reasons.append(f"singleton ({only})")
    return reasons


_JUDGE_RESPONSE_RE = re.compile(
    r"##\s*Cluster\s+(\d+)\s*\n"
    r".*?-\s*Verdict:\s*(real|smell|nit|wrong)\b"
    r".*?-\s*Confidence:\s*(high|medium|low)\b"
    r".*?-\s*Reason:\s*(.+?)(?=\n##|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def parse_judge_response(text: str, expected_id: int) -> dict | None:
    """Extract verdict / confidence / reason from a judge response."""
    m = _JUDGE_RESPONSE_RE.search(text)
    if not m:
        return None
    return {
        "cluster_id": int(m.group(1)),
        "verdict": m.group(2).lower(),
        "confidence": m.group(3).lower(),
        "reason": m.group(4).strip().replace("\n", " "),
    }


def render_verdicts_draft(
    output: Path,
    drafts: list[dict],
    judge_model: str,
) -> None:
    """Render verdicts.draft.md with attention preamble + per-cluster blocks."""
    attention = [d for d in drafts if d["flags"]]
    rest = [d for d in drafts if not d["flags"]]

    out = [
        "# Verdicts (DRAFT — human review required)",
        "",
        f"Drafted by `{judge_model}` via OpenRouter. **The human reviewer owns the",
        "final call.** Read this file, override any verdicts you disagree with,",
        "then save as `verdicts.md` before running `compute_metrics.py`.",
        "",
    ]

    if attention:
        out.append(f"## Needs human attention ({len(attention)} of {len(drafts)} clusters)")
        out.append("")
        for d in attention:
            reasons = " · ".join(d["flags"])
            topic = d.get("topic") or ""
            topic_short = topic[:80].replace("\n", " ")
            out.append(f"- **#{d['cluster_id']}** {topic_short} — _{reasons}_")
        out.append("")
        out.append(f"High-confidence clusters ({len(rest)}): listed below, scan for sanity.")
        out.append("")
    else:
        out.append("All clusters drafted with no attention flags. Still — verify before saving as `verdicts.md`.")
        out.append("")

    out.append("---")
    out.append("")

    for d in drafts:
        marker = " ⚠" if d["flags"] else ""
        out.append(f"## Cluster {d['cluster_id']}{marker}")
        if d["flags"]:
            out.append(f"<!-- attention: {' · '.join(d['flags'])} -->")
        out.append(f"- Verdict: {d['verdict']}")
        out.append(f"- Confidence: {d['confidence']}")
        out.append(f"- Reason: {d['reason']}")
        out.append("")

    output.write_text("\n".join(out), encoding="utf-8")


def cmd_adjudicate(args) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: set OPENROUTER_API_KEY"); sys.exit(1)

    findings = json.loads(args.findings.read_text(encoding="utf-8"))
    issues = findings["issues"]
    clusters_data = json.loads(args.clusters.read_text(encoding="utf-8"))
    clusters = clusters_data["clusters"]
    print(f"Clusters to adjudicate: {len(clusters)}")

    repo_path = Path(args.repo_path).resolve() if args.repo_path else None
    if repo_path and not repo_path.is_dir():
        print(f"Error: --repo-path is not a directory: {repo_path}")
        sys.exit(1)

    template = args.prompt.read_text(encoding="utf-8")

    drafts = []
    for i, cluster in enumerate(clusters, 1):
        cid = cluster["id"]
        members = [issues[idx] for idx in cluster.get("members", [])
                   if 0 <= idx < len(issues)]
        location = pick_best_location(members)
        excerpt, status = build_source_excerpt(
            repo_path, location, args.context_lines)

        prompt = (template
                  .replace("{cluster_id}", str(cid))
                  .replace("{cluster_topic}", cluster.get("topic", ""))
                  .replace("{cluster_severity}", cluster.get("consensus_severity", "?"))
                  .replace("{source_status}", status)
                  .replace("{cluster_findings}", render_cluster_findings(members))
                  .replace("{source_excerpt}", excerpt))

        print(f"[{i}/{len(clusters)}] cluster #{cid} (source: {status})...",
              end=" ", flush=True)
        res = call_openrouter(args.judge_model, prompt, api_key,
                              timeout=args.timeout, max_tokens=args.max_tokens)
        if res["status"] != "ok":
            print(f"FAIL — {res.get('error') or res.get('http_code')}")
            drafts.append({
                "cluster_id": cid, "topic": cluster.get("topic", ""),
                "verdict": "wrong", "confidence": "low",
                "reason": f"(judge call failed: {res.get('error') or res.get('http_code')})",
                "flags": ["judge call failed"],
            })
            continue

        parsed = parse_judge_response(res["content"], cid)
        if not parsed:
            print(f"FAIL — could not parse response")
            drafts.append({
                "cluster_id": cid, "topic": cluster.get("topic", ""),
                "verdict": "wrong", "confidence": "low",
                "reason": "(judge response did not match expected format)",
                "flags": ["unparseable response"],
            })
            continue

        if status == "unavailable" and parsed["confidence"] != "low":
            parsed["confidence"] = "low"

        flags = attention_flags(cluster, members, parsed["confidence"])
        if status == "unavailable":
            flags.insert(0, "source unavailable")

        drafts.append({
            "cluster_id": cid,
            "topic": cluster.get("topic", ""),
            "verdict": parsed["verdict"],
            "confidence": parsed["confidence"],
            "reason": parsed["reason"],
            "flags": flags,
        })
        print(f"{parsed['verdict']} ({parsed['confidence']}) — {res['elapsed_sec']}s")

        if i < len(clusters):
            time.sleep(args.sleep)

    render_verdicts_draft(args.output, drafts, args.judge_model)
    by_cat = Counter(d["verdict"] for d in drafts)
    by_conf = Counter(d["confidence"] for d in drafts)
    flagged = sum(1 for d in drafts if d["flags"])
    print(f"\nVerdicts draft: {args.output}")
    print(f"  By category:   {dict(by_cat)}")
    print(f"  By confidence: {dict(by_conf)}")
    print(f"  Flagged for human attention: {flagged}/{len(drafts)}")


# ── CLI ─────────────────────────────────────────────────────────────────────

DEFAULT_CLUSTER_PROMPT = Path(__file__).parent / "prompts" / "cluster.en.txt"
DEFAULT_JUDGE_PROMPT = Path(__file__).parent / "prompts" / "judge.en.txt"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description="Optional automated draft for clustering and adjudication.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("cluster", help="Draft clusters.json from findings.json")
    pc.add_argument("--findings", type=Path, default=Path("findings.json"))
    pc.add_argument("--output", "-o", type=Path, default=Path("clusters.json"))
    pc.add_argument("--prompt", type=Path, default=DEFAULT_CLUSTER_PROMPT)
    pc.add_argument("--judge-model", default="openai/gpt-5.5",
                    help="OpenRouter model id for the judge.")
    pc.add_argument("--timeout", type=int, default=600)
    pc.add_argument("--max-tokens", type=int, default=16000)
    pc.set_defaults(func=cmd_cluster)

    pa = sub.add_parser("adjudicate", help="Draft verdicts.draft.md from clusters + source")
    pa.add_argument("--clusters", type=Path, default=Path("clusters.json"))
    pa.add_argument("--findings", type=Path, default=Path("findings.json"))
    pa.add_argument("--repo-path", type=str, default=None,
                    help="Path to source repo for reading code excerpts.")
    pa.add_argument("--context-lines", type=int, default=50,
                    help="Lines of source ±N around the cited line (default 50).")
    pa.add_argument("--output", "-o", type=Path, default=Path("verdicts.draft.md"))
    pa.add_argument("--prompt", type=Path, default=DEFAULT_JUDGE_PROMPT)
    pa.add_argument("--judge-model", default="openai/gpt-5.5",
                    help="OpenRouter model id for the judge.")
    pa.add_argument("--timeout", type=int, default=600)
    pa.add_argument("--max-tokens", type=int, default=4000)
    pa.add_argument("--sleep", type=float, default=2.0,
                    help="Seconds between per-cluster judge calls.")
    pa.set_defaults(func=cmd_adjudicate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
