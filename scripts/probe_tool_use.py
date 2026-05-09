#!/usr/bin/env python3
"""Probe which OpenRouter models honor function-calling (A0.3).

For each model in `models.json`, send a trivial tool-use prompt:
  - tool: `echo(text: string) -> string`
  - user: "Use the echo tool to repeat the word 'banana'."

If the model returns a valid `message.tool_calls` array with our tool —
it supports tool use. If it returns plain content without tool_calls,
it ignores the tool. Errors and parse failures are recorded.

The result is written to two files:
  - models.agentic.json — filtered subset that passes (drop-in input
    for code_review_benchmark_agent.py --models-file)
  - runs/.probe/probe_<timestamp>.json — full report for diagnosis

This script does NOT depend on Serena or the `mcp` package. It only
needs `requests` and `OPENROUTER_API_KEY`.

Usage:
    python scripts/probe_tool_use.py
    python scripts/probe_tool_use.py --models-file models.json -o models.agentic.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Repeats the given text verbatim. Use this to acknowledge a word.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to echo back.",
                },
            },
            "required": ["text"],
        },
    },
}
PROBE_USER_MESSAGE = (
    "Use the `echo` tool to repeat the word 'banana'. "
    "Do not answer in plain text — call the tool."
)


def _load_models(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def probe_one(model_id: str, api_key: str, timeout: int = 60) -> dict:
    """Send a tool-use probe and classify the response."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get(
            "OPENROUTER_REFERER",
            "https://github.com/svetkis/ai-code-review-benchmark",
        ),
        "X-Title": "Code Review Benchmark (probe)",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": PROBE_USER_MESSAGE}],
        "tools": [PROBE_TOOL],
        "tool_choice": "auto",
        "max_tokens": 200,
        "temperature": 0.0,
    }
    t0 = time.time()
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload,
                          timeout=timeout)
    except requests.exceptions.Timeout:
        return {"tool_use_ok": False, "reason": "timeout",
                "elapsed_sec": timeout}
    except Exception as e:  # noqa: BLE001 — provider errors vary
        return {"tool_use_ok": False, "reason": f"exception:{type(e).__name__}",
                "error": str(e)[:200], "elapsed_sec": round(time.time() - t0, 2)}

    elapsed = round(time.time() - t0, 2)
    if r.status_code != 200:
        return {"tool_use_ok": False, "reason": f"http_{r.status_code}",
                "error": r.text[:300], "elapsed_sec": elapsed}

    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    if not tool_calls:
        return {"tool_use_ok": False, "reason": "no_tool_calls",
                "content_preview": content[:200], "elapsed_sec": elapsed}

    # Validate the tool call shape.
    tc = tool_calls[0]
    fn = (tc.get("function") or {})
    name = fn.get("name")
    if name != "echo":
        return {"tool_use_ok": False, "reason": "wrong_tool_name",
                "got": name, "elapsed_sec": elapsed}
    args_raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except json.JSONDecodeError:
        return {"tool_use_ok": False, "reason": "args_not_json",
                "args_raw": str(args_raw)[:200], "elapsed_sec": elapsed}
    if "text" not in args:
        return {"tool_use_ok": False, "reason": "missing_required_arg",
                "args": args, "elapsed_sec": elapsed}

    return {"tool_use_ok": True, "elapsed_sec": elapsed,
            "args_text_preview": str(args.get("text"))[:50]}


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description="Probe OpenRouter tool-use support per model (A0.3)."
    )
    p.add_argument("--models-file", default="models.json", type=Path)
    p.add_argument("--output", "-o", default="models.agentic.json", type=Path,
                   help="Path to write the filtered models.agentic.json")
    p.add_argument("--report-dir", default="runs/.probe", type=Path,
                   help="Where to write the full probe_<timestamp>.json report")
    p.add_argument("--timeout", type=int, default=60,
                   help="Per-model HTTP timeout in seconds")
    p.add_argument("--sleep", type=float, default=0.5,
                   help="Seconds between probe requests (default: 0.5)")
    p.add_argument("--models", "-m", nargs="*", default=None,
                   help="Subset of model display names to probe. Default: all.")
    args = p.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: set OPENROUTER_API_KEY", file=sys.stderr)
        return 2
    if not args.models_file.exists():
        print(f"Error: {args.models_file} not found", file=sys.stderr)
        return 2

    available = _load_models(args.models_file)
    if args.models:
        selected = {n: m for n, m in available.items() if n in args.models}
        if not selected:
            print("Error: no matching models", file=sys.stderr)
            return 2
    else:
        selected = available

    print(f"Probing {len(selected)} models for tool-use compat...\n")
    report: dict[str, dict] = {}
    passing: dict[str, str] = {}
    for i, (name, model_id) in enumerate(selected.items(), 1):
        print(f"[{i}/{len(selected)}] {name} ({model_id})...", end=" ", flush=True)
        result = probe_one(model_id, api_key, timeout=args.timeout)
        result["model_id"] = model_id
        report[name] = result
        if result["tool_use_ok"]:
            passing[name] = model_id
            print(f"OK ({result['elapsed_sec']}s)")
        else:
            reason = result.get("reason", "unknown")
            print(f"FAIL — {reason}")
        if i < len(selected):
            time.sleep(args.sleep)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"probe_{timestamp}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump({"timestamp": timestamp, "models": report}, f,
                  ensure_ascii=False, indent=2)

    out = {
        "_comment": (
            "Models confirmed to support OpenRouter function-calling. "
            f"Generated by scripts/probe_tool_use.py at {timestamp}. "
            "Re-run the probe after upgrading the model fleet."
        ),
        **passing,
    }
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\nPassing: {len(passing)}/{len(selected)}")
    print(f"Report: {report_path}")
    print(f"Filtered models: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
