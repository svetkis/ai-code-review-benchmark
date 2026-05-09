#!/usr/bin/env python3
"""
Code Review Benchmark — run a diff through several models on OpenRouter.

Usage:
  python code_review_benchmark.py <diff_path>
      [--context-file PATH ...]
      [--output PATH]
      [--models NAME ...]
      [--timeout SECONDS]
      [--prompt PATH]
      [--models-file PATH]

Environment:
  OPENROUTER_API_KEY  — OpenRouter API key (required)
  OPENROUTER_REFERER  — optional HTTP-Referer header (default: GitHub repo URL)

Output:
  results_<timestamp>.json — metadata + raw responses from every model.
  results_<timestamp>/<model>.md — one markdown review per model.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing 'requests' package: pip install requests")
    sys.exit(1)


# ── Models ──────────────────────────────────────────────────────────────────

DEFAULT_MODELS_FILE = Path(__file__).parent / "models.json"


def load_models(path: Path) -> dict[str, str]:
    """Load models from a JSON file: {display_name: openrouter_model_id}.

    Keys starting with "_" are treated as comments and skipped.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ── Prompt template ─────────────────────────────────────────────────────────

DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "review.en.txt"


def _build_context_block(context_files: list[tuple[str, str]]) -> str:
    if not context_files:
        return ""
    parts = ["", "Full contents of the modified files (for context):"]
    for display_path, content in context_files:
        parts.append("")
        parts.append(f"--- {display_path} ---")
        parts.append("```")
        parts.append(content)
        parts.append("```")
    return "\n".join(parts)


def build_user_prompt(
    diff_text: str,
    context_files: list[tuple[str, str]],
    template_path: Path = DEFAULT_PROMPT_PATH,
) -> str:
    """Render the prompt template at template_path with diff and context_files.

    The template uses two placeholders:
      {diff}          — replaced with the unified diff text
      {context_block} — replaced with the formatted context-files block (or "")

    The default parser expects the English markers used in review.en.txt
    (`Findings:`, `Location:`, `Why it matters:`, `Evidence:`, `Recommendation:`).
    If you switch to a non-English template (e.g. review.ru.txt), update the
    parser regexes in this file and in aggregate_findings.py to match.
    """
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        diff=diff_text,
        context_block=_build_context_block(context_files),
    )


# ── OpenRouter call ─────────────────────────────────────────────────────────

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def call_model(
    model_id: str,
    diff_text: str,
    context_files: list[tuple[str, str]],
    api_key: str,
    timeout: int = 600,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get(
            "OPENROUTER_REFERER",
            "https://github.com/svetkis/ai-code-review-benchmark",
        ),
        "X-Title": "Code Review Benchmark",
    }
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": build_user_prompt(diff_text, context_files, prompt_path),
            },
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
        "usage": {"include": True},
    }

    t0 = time.time()
    try:
        resp = requests.post(
            OPENROUTER_URL, headers=headers, json=payload, timeout=timeout
        )
        elapsed = round(time.time() - t0, 2)

        if resp.status_code != 200:
            return {
                "status": "error",
                "http_code": resp.status_code,
                "error": resp.text[:1000],
                "elapsed_sec": elapsed,
            }

        data = resp.json()
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        usage = data.get("usage", {}) or {}
        completion_details = usage.get("completion_tokens_details") or {}

        issues = parse_issues(content)

        return {
            "status": "ok",
            "content": content,
            "issues": issues,
            "issues_count": len(issues),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cost": usage.get("cost"),
                "reasoning_tokens": completion_details.get("reasoning_tokens"),
            },
            "elapsed_sec": elapsed,
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "error": "timeout", "elapsed_sec": timeout}
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "elapsed_sec": round(time.time() - t0, 2),
        }


# ── Findings parser ─────────────────────────────────────────────────────────

ISSUE_HEADER_RE = re.compile(
    r"^[ \t]*(\d+)\)[ \t]*"
    r"(?:\[severity:[ \t]*([^\]]+)\])?[ \t]*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_issues(text: str) -> list[dict]:
    """
    Locate blocks shaped like:
        Findings:
        1) [severity: ...] Brief summary
          - Location: ...
          - Why it matters: ...
          - Evidence: ...
          - Recommendation: ...
    Returns a list of dicts; empty if nothing matched.
    """
    # Restrict parsing to the section after "Findings:" — otherwise "1)" lines
    # from other numbered lists in the response would be picked up.
    m_section = re.search(r"^Findings\s*:\s*$", text, re.MULTILINE | re.IGNORECASE)
    section = text[m_section.end():] if m_section else text

    matches = list(ISSUE_HEADER_RE.finditer(section))
    if not matches:
        return []

    issues = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        body = section[body_start:body_end].strip()

        severity = (m.group(2) or "").strip().lower() or None
        summary = (m.group(3) or "").strip() or None

        place = _extract_field(body, "Location")
        why = _extract_field(body, "Why it matters")
        evidence = _extract_field(body, "Evidence")
        recommendation = _extract_field(body, "Recommendation")

        issues.append({
            "n": int(m.group(1)),
            "severity": severity,
            "summary": summary,
            "place": place,
            "why": why,
            "evidence": evidence,
            "recommendation": recommendation,
        })
    return issues


_FIELD_START_RE = re.compile(r"^\s*[-*]\s*([^:]+?)\s*:\s*(.*)$")


def _extract_field(body: str, name: str) -> str | None:
    """
    Line-by-line: find '- <name>: rest', then collect continuation lines
    (indented / no bullet) until the next '- <Word>:' or end of body.
    """
    name_lower = name.lower()
    collected: list[str] = []
    in_field = False
    for line in body.splitlines():
        m = _FIELD_START_RE.match(line)
        if m:
            field_name = m.group(1).strip().lower()
            if in_field:
                # Next field started — stop collecting.
                break
            if field_name == name_lower:
                rest = m.group(2).strip()
                if rest:
                    collected.append(rest)
                in_field = True
        elif in_field:
            stripped = line.strip()
            if stripped:
                collected.append(stripped)
    if not collected:
        return None
    return " ".join(collected) or None


# ── Summary table ───────────────────────────────────────────────────────────

def print_summary(results: dict):
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    header = (
        f"{'Model':<22} {'Status':<8} {'Issues':<7} "
        f"{'B':<3} {'M':<3} {'m':<3} {'n':<3} "
        f"{'Time':<8} {'Tokens':<10}"
    )
    print(header)
    print("-" * 90)

    for name, res in results.items():
        if res["status"] != "ok":
            print(
                f"{name:<22} {'FAIL':<8} {'-':<7} "
                f"{'-':<3} {'-':<3} {'-':<3} {'-':<3} "
                f"{str(res.get('elapsed_sec','?')):<8} {'-':<10}"
            )
            continue

        issues = res.get("issues") or []
        sev = {"blocker": 0, "major": 0, "minor": 0, "nit": 0}
        for issue in issues:
            s = (issue.get("severity") or "").lower()
            for key in sev:
                if key in s:
                    sev[key] += 1
                    break

        tokens = res.get("usage", {}).get("total_tokens") or "-"
        print(
            f"{name:<22} {'OK':<8} {len(issues):<7} "
            f"{sev['blocker']:<3} {sev['major']:<3} {sev['minor']:<3} {sev['nit']:<3} "
            f"{str(res['elapsed_sec']):<8} {str(tokens):<10}"
        )

    print("=" * 90)
    print("B=blocker, M=major, m=minor, n=nit")


# ── Per-model markdown output ──────────────────────────────────────────────

def save_per_model_markdown(out_dir: Path, results: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, res in results.items():
        safe_name = re.sub(r"[^\w\-]+", "_", name).strip("_")
        path = out_dir / f"{safe_name}.md"
        if res["status"] != "ok":
            path.write_text(
                f"# {name}\n\n"
                f"**Status:** FAIL\n\n"
                f"**Error:** {res.get('error') or res.get('http_code')}\n\n"
                f"**Time:** {res.get('elapsed_sec')} s\n",
                encoding="utf-8",
            )
            continue

        usage = res.get("usage") or {}
        header = (
            f"# {name}\n\n"
            f"**Status:** OK  \n"
            f"**Time:** {res.get('elapsed_sec')} s  \n"
            f"**Tokens:** prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}  \n"
            f"**Findings parsed:** {res.get('issues_count')}\n\n"
            f"---\n\n"
        )
        path.write_text(header + (res.get("content") or ""), encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # On Windows the console is cp1252/cp866 by default — non-ASCII in --help
    # would crash. Force UTF-8.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Code Review Benchmark via OpenRouter"
    )
    parser.add_argument("diff", help="Path to a unified diff file")
    parser.add_argument(
        "--context-file", "-c", action="append", default=[],
        help="Path to a context file (post-change content). Repeatable.",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Path for the JSON output (default: results_<timestamp>.json)",
    )
    parser.add_argument(
        "--models", "-m", nargs="*", default=None,
        help="Subset of model display names to run. Default: all.",
    )
    parser.add_argument(
        "--timeout", "-t", type=int, default=600,
        help="Per-model timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--prompt", "-p", default=str(DEFAULT_PROMPT_PATH),
        help=(
            "Path to the prompt template (placeholders: {diff}, "
            "{context_block}). Default: prompts/review.en.txt"
        ),
    )
    parser.add_argument(
        "--models-file", default=str(DEFAULT_MODELS_FILE),
        help=(
            "Path to JSON with the model list "
            "{display_name: openrouter_model_id}. Default: models.json"
        ),
    )
    args = parser.parse_args()
    prompt_path = Path(args.prompt)
    if not prompt_path.is_file():
        print(f"Error: prompt file not found: {prompt_path}")
        sys.exit(1)
    models_file = Path(args.models_file)
    if not models_file.is_file():
        print(f"Error: models file not found: {models_file}")
        sys.exit(1)
    available_models = load_models(models_file)
    if not available_models:
        print(f"Error: no models defined in {models_file}")
        sys.exit(1)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: set the OPENROUTER_API_KEY environment variable")
        sys.exit(1)

    diff_path = Path(args.diff)
    if not diff_path.exists():
        print(f"Error: diff file not found: {diff_path}")
        sys.exit(1)
    diff_text = diff_path.read_text(encoding="utf-8")

    context_files: list[tuple[str, str]] = []
    for cf in args.context_file:
        cf_path = Path(cf)
        if not cf_path.exists():
            print(f"Error: context file not found: {cf_path}")
            sys.exit(1)
        context_files.append((str(cf_path), cf_path.read_text(encoding="utf-8")))

    total_chars = len(diff_text) + sum(len(c) for _, c in context_files)
    print(f"Diff: {diff_path} ({len(diff_text)} chars)")
    print(f"Context files: {len(context_files)}")
    print(f"Total chars: {total_chars} (~{total_chars // 4} tokens)")

    if args.models:
        selected = {n: m for n, m in available_models.items() if n in args.models}
        if not selected:
            print(
                "Error: no matching models. Available:\n"
                + "\n".join(f"  - {n}" for n in available_models.keys())
            )
            sys.exit(1)
    else:
        selected = available_models

    print(f"Models: {len(selected)}\n")

    results: dict = {}
    for i, (name, model_id) in enumerate(selected.items(), 1):
        print(f"[{i}/{len(selected)}] {name} ({model_id})...", end=" ", flush=True)
        res = call_model(
            model_id, diff_text, context_files, api_key,
            timeout=args.timeout, prompt_path=prompt_path,
        )
        results[name] = res

        if res["status"] == "ok":
            print(
                f"OK — {res.get('issues_count', 0)} findings — "
                f"{res['elapsed_sec']}s"
            )
        else:
            print(f"FAIL — {str(res.get('error', 'unknown'))[:80]}")

        if i < len(selected):
            time.sleep(2)

    print_summary(results)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else Path(f"results_{timestamp}.json")
    output_data = {
        "meta": {
            "diff_file": str(diff_path),
            "diff_size_chars": len(diff_text),
            "context_files": [p for p, _ in context_files],
            "context_size_chars": sum(len(c) for _, c in context_files),
            "prompt_template": str(prompt_path),
            "models_file": str(models_file),
            "timestamp": timestamp,
            "models_count": len(selected),
        },
        "results": results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    md_dir = output_path.with_suffix("")
    save_per_model_markdown(md_dir, results)

    print(f"\nJSON: {output_path}")
    print(f"Per-model markdown: {md_dir}/")


if __name__ == "__main__":
    main()
