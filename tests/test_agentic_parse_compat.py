"""A2.2 verification: agentic-track markdown parses through aggregate_findings.

The agentic prompt scaffold mirrors the bounded-context findings format
(per Q1 decision in docs/plans/2026-05-09-agentic-track.md). This test
proves that the existing parser handles agentic output without changes —
including the "Evidence: <code> + tool calls that confirmed it" wording
specific to the agentic prompt.

    python -m unittest tests.test_agentic_parse_compat
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregate_findings import parse_md  # noqa: E402


SAMPLE_AGENTIC_MARKDOWN = """\
# deepseek-v3

**Status:** OK
**Time:** 47.3 s
**Tokens:** prompt=12500 completion=2400 total=14900
**Cost:** $0.0084
**Findings parsed:** 2

---

Brief summary:
- Overall: needs work
- Main risks: silent error handling around DB writes; missing input bounds.

Findings:
1) [severity: major] DB write swallows exceptions, masking real failures
  - Location: src/db/writer.py:42 (DBWriter.write)
  - Why it matters: callers cannot retry or report; data loss is silent.
  - Evidence: see `except Exception: pass` block; confirmed via
    find_referencing_symbols showing 4 callers expecting exceptions.
  - Recommendation: re-raise after logging, or wrap in a Result type.

2) [severity: minor] Missing upper bound on `--retries` CLI flag
  - Location: src/cli.py:18 (build_parser)
  - Why it matters: a user passing `--retries 1000000` will hang the worker.
  - Evidence: read_file showed argparse with no `choices=` or upper bound.
  - Recommendation: clamp to a reasonable max (e.g. 100) or document.
"""


class TestAgenticParserCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        )
        self.tmp.write(SAMPLE_AGENTIC_MARKDOWN)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_two_findings_extracted(self):
        issues = parse_md(self.path)
        self.assertEqual(len(issues), 2)

    def test_severity_normalized_lowercase(self):
        issues = parse_md(self.path)
        self.assertEqual(issues[0]["severity"], "major")
        self.assertEqual(issues[1]["severity"], "minor")

    def test_location_field_extracted(self):
        issues = parse_md(self.path)
        self.assertIn("src/db/writer.py:42", issues[0]["location"])
        self.assertIn("DBWriter.write", issues[0]["location"])

    def test_evidence_with_tool_call_reference_preserved(self):
        issues = parse_md(self.path)
        self.assertIn("find_referencing_symbols", issues[0]["evidence"])

    def test_recommendation_field_extracted(self):
        issues = parse_md(self.path)
        self.assertIn("re-raise", issues[0]["recommendation"])
        self.assertIn("clamp", issues[1]["recommendation"])


if __name__ == "__main__":
    unittest.main()
