"""
LLM prompts for the Dev Agent.

build_suggestion() — sent to the Anthropic API to generate a human-readable
    fix suggestion that is posted as a GitHub Issue comment.
"""


def build_suggestion(
    error_type: str,
    error_message: str,
    summary: str,
    test_file_path: str,
    test_name: str,
    test_content: str,
    source_files: dict[str, str],
) -> str:
    """
    Build the fix-suggestion prompt for the Anthropic API call.

    The response is posted as a GitHub Issue comment for the engineering team
    to review.

    Args:
        error_type:     Exception class, e.g. "AttributeError".
        error_message:  Exception message.
        summary:        Plain-English crash summary from the Crash Handler.
        test_file_path: Path to the failing test file.
        test_name:      Failing test function name.
        test_content:   Full content of the failing test.
        source_files:   Mapping of file path → content for relevant source files.

    Returns:
        Prompt string ready to send to the Anthropic API.
    """
    source_section = ""
    if source_files:
        source_section = "\n## Relevant Source Files\n"
        for path, content in source_files.items():
            source_section += f"\n### `{path}`\n```python\n{content}\n```\n"
    else:
        source_section = "\n## Relevant Source Files\n_(No source files available.)_\n"

    return f"""\
You are a senior software engineer reviewing a production bug.

## Bug Summary
- **Error type:** {error_type}
- **Error message:** {error_message}
- **Summary:** {summary}

## Failing Test
The following test was written to reproduce this bug. It currently fails.

**File:** `{test_file_path}`
**Test:** `{test_name}`

```python
{test_content}
```
{source_section}
## Your Task

Write the minimal code change that makes the failing test pass without breaking other functionality.

The test asserts the CORRECT behaviour — it does not assert that an exception is raised.
Your fix must make the function return the expected value instead of crashing.

Your response must:
1. Identify the root cause in one sentence.
2. Show the exact code change using clearly labelled BEFORE and AFTER blocks.
   - The AFTER block MUST include the defensive guard or fix — it must not be
     identical to the BEFORE block.
   - Example of a correct guard for a None-dereference bug:
     BEFORE: `return f"Hello, {{user.get('name')}}!"`
     AFTER:  `if user is None: return None` (then the original return)
3. Explain why this change makes the test pass in 2–3 sentences.

Be concise. Do not refactor unrelated code. Do not add new dependencies.
"""
