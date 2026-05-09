#!/usr/bin/env python3
"""
migrate_run_markers.py — translate Russian markers to English inside an
existing run folder, in-place.

Why: the methodology used Russian markers (Замечания: / Место: / …) in early
runs. The default parser now expects English markers (Findings: / Location:
/ …). This script rewrites the markers in saved markdown/json artifacts so
that compute_metrics.py and aggregate_findings.py can re-process the older
run with the current code.

Scope:
  - .md files anywhere under the target dir
  - .json files (string-level replacement; only well-known marker words)

What it does NOT touch:
  - input.diff (just the diff)
  - cluster topic strings or your handwritten worklist text
    (only marker words are replaced; arbitrary Russian prose is kept)

Backups:
  Every modified file is copied to <name>.bak.<timestamp> before the rewrite.

Usage:
  python scripts/migrate_run_markers.py runs/<run-id>
  python scripts/migrate_run_markers.py runs/<run-id> --dry-run
"""

import argparse
import shutil
import sys
import time
from pathlib import Path


# Order matters: longer phrases first so "Почему важно" beats "Почему".
MARKER_REPLACEMENTS = [
    ("Замечания:",      "Findings:"),
    ("Почему важно:",   "Why it matters:"),
    ("Доказательство:", "Evidence:"),
    ("Рекомендация:",   "Recommendation:"),
    ("Место:",          "Location:"),
    ("Краткое резюме:", "Brief summary:"),
    ("Главные риски:",  "Main risks:"),
    ("Итог:",           "Overall:"),
    # JSON keys produced by the older parser
    ('"место":',           '"location":'),
    ('"почему_важно":',    '"why_it_matters":'),
    ('"доказательство":',  '"evidence":'),
    ('"рекомендация":',    '"recommendation":'),
]


def replace_in_text(text: str) -> tuple[str, int]:
    total = 0
    for src, dst in MARKER_REPLACEMENTS:
        count = text.count(src)
        if count:
            text = text.replace(src, dst)
            total += count
    return text, total


def process_file(path: Path, dry_run: bool, ts: str) -> int:
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        print(f"  ! skip {path}: {e}")
        return 0
    new_text, count = replace_in_text(original)
    if count == 0:
        return 0
    if dry_run:
        print(f"  [dry] {path}: {count} replacements")
        return count
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, backup)
    path.write_text(new_text, encoding="utf-8")
    print(f"  {path}: {count} replacements (backup: {backup.name})")
    return count


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path, help="Path to runs/<run-id>")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing")
    args = ap.parse_args()

    if not args.run_dir.is_dir():
        print(f"Not a directory: {args.run_dir}")
        sys.exit(1)

    ts = time.strftime("%Y%m%d_%H%M%S")
    total_files = 0
    total_replacements = 0
    for ext in ("md", "json"):
        for path in sorted(args.run_dir.rglob(f"*.{ext}")):
            if any(part.startswith(".bak") or ".bak." in part for part in path.parts):
                continue
            n = process_file(path, args.dry_run, ts)
            if n:
                total_files += 1
                total_replacements += n

    verb = "would touch" if args.dry_run else "modified"
    print(f"\n{verb} {total_files} files, {total_replacements} replacements")
    if args.dry_run:
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
