"""Unit tests for the MCP -> OpenAI tool schema converter.

Runnable without installing `mcp` (the converter operates on plain dicts).

    python -m unittest tests.test_agentic_schema
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the repo root importable when running from `tests/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code_review_benchmark_agent import (  # noqa: E402
    CURATED_TOOLS,
    mcp_to_openai_tool,
    render_tool_descriptions,
)


def _serena_like(name: str) -> dict:
    """A representative MCP tool spec, shaped like Serena's real output."""
    return {
        "name": name,
        "description": (
            "Get a high-level understanding of the code symbols in a file.\n"
            "Returns a JSON object containing symbols grouped by kind."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "title": "Relative Path",
                    "description": "The relative path to the file.",
                },
                "depth": {
                    "default": 0,
                    "type": "integer",
                    "title": "Depth",
                    "description": "Depth up to which descendants are retrieved.",
                },
            },
            "required": ["relative_path"],
            "title": "applyArguments",
        },
    }


class TestMcpToOpenAITool(unittest.TestCase):
    def test_envelope_shape(self):
        out = mcp_to_openai_tool(_serena_like("get_symbols_overview"))
        self.assertEqual(out["type"], "function")
        self.assertIn("function", out)
        fn = out["function"]
        self.assertEqual(fn["name"], "get_symbols_overview")
        self.assertIn("description", fn)
        self.assertIn("parameters", fn)
        self.assertEqual(fn["parameters"]["type"], "object")

    def test_schema_body_passes_through(self):
        spec = _serena_like("find_symbol")
        out = mcp_to_openai_tool(spec)
        params = out["function"]["parameters"]
        self.assertEqual(params["required"], ["relative_path"])
        self.assertEqual(
            params["properties"]["depth"]["type"], "integer"
        )
        self.assertEqual(params["properties"]["depth"]["default"], 0)

    def test_none_description_coerced_to_empty(self):
        spec = {"name": "list_dir", "description": None,
                "inputSchema": {"type": "object", "properties": {}}}
        out = mcp_to_openai_tool(spec)
        self.assertEqual(out["function"]["description"], "")

    def test_missing_input_schema_gets_object_stub(self):
        spec = {"name": "no_args_tool", "description": "x"}
        out = mcp_to_openai_tool(spec)
        self.assertEqual(out["function"]["parameters"]["type"], "object")
        self.assertEqual(out["function"]["parameters"]["properties"], {})

    def test_schema_without_type_field_is_normalized(self):
        spec = {
            "name": "weird",
            "description": "x",
            "inputSchema": {"properties": {"x": {"type": "string"}}},
        }
        out = mcp_to_openai_tool(spec)
        self.assertEqual(out["function"]["parameters"]["type"], "object")
        self.assertIn("x", out["function"]["parameters"]["properties"])

    def test_full_curated_set_converts(self):
        for name in CURATED_TOOLS:
            with self.subTest(tool=name):
                out = mcp_to_openai_tool(_serena_like(name))
                self.assertEqual(out["type"], "function")
                self.assertEqual(out["function"]["name"], name)
                self.assertIsInstance(out["function"]["parameters"], dict)


class TestToolDescriptionsBlock(unittest.TestCase):
    def test_one_line_per_tool(self):
        tools = [_serena_like(n) for n in CURATED_TOOLS]
        block = render_tool_descriptions(tools)
        self.assertEqual(len(block.splitlines()), len(CURATED_TOOLS))
        for name in CURATED_TOOLS:
            self.assertIn(name, block)

    def test_only_first_description_line_used(self):
        tools = [_serena_like("get_symbols_overview")]
        block = render_tool_descriptions(tools)
        self.assertNotIn("\nReturns a JSON", block)
        self.assertIn("Get a high-level understanding", block)


if __name__ == "__main__":
    unittest.main()
