"""
Utility functions shared across the Helix agent pipeline.

These are intentionally small and focused — each function does one thing.
"""

import json
import re


def extract_json(text: str) -> dict:
    """
    Extract the first JSON object from a string that may contain prose and code blocks.

    LLMs commonly wrap JSON responses in markdown code fences:

        ```json
        {"key": "value"}
        ```

    This function strips the fences if present and parses the JSON.

    Args:
        text: Raw LLM output, possibly containing markdown and prose.

    Returns:
        Parsed JSON as a dict.

    Raises:
        ValueError: If no valid JSON object is found in the text.
    """
    # Try to extract JSON from a markdown code block first.
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        candidate = code_block.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Fall back: find the first { ... } span in the raw text.
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON object found in LLM output:\n{text[:500]}")
