# verify_prompt_mtf.py
# ==============================================================================
# VERBATIM DIFF — MTF INTERPRETER
# Run this BEFORE deleting MTF_INTERPRETER_SYSTEM_PROMPT from mtf_interpreter.py.
# Confirms the MD body matches the Python constant character-for-character
# (accounting for backslash line-continuation stripping).
#
# Usage:  python verify_prompt_mtf.py
# Pass:   prints OK and character count
# Fail:   prints first diff location and exits with code 1
# ==============================================================================

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from mtf_interpreter import MTF_INTERPRETER_SYSTEM_PROMPT
from agent_core import load_agent_spec

spec = load_agent_spec("mtf_interpreter")
py_prompt = MTF_INTERPRETER_SYSTEM_PROMPT
md_body   = spec.body

if py_prompt == md_body:
    print(f"OK — prompts are character-identical ({len(py_prompt)} chars).")
    sys.exit(0)

# Find first difference
min_len = min(len(py_prompt), len(md_body))
first_diff = next(
    (i for i in range(min_len) if py_prompt[i] != md_body[i]),
    min_len,
)

# Show context around first diff
ctx_start = max(0, first_diff - 60)
ctx_end   = min(min_len, first_diff + 60)

print("MISMATCH — prompts differ.")
print(f"  Python length : {len(py_prompt)}")
print(f"  MD body length: {len(md_body)}")
print(f"  First diff at char {first_diff}")
print(f"\n  Python [{ctx_start}:{ctx_end}]:")
print(f"    {repr(py_prompt[ctx_start:ctx_end])}")
print(f"\n  MD     [{ctx_start}:{ctx_end}]:")
print(f"    {repr(md_body[ctx_start:ctx_end])}")

# Known acceptable differences: Python line-continuation artifacts produce
# extra spaces at join points (e.g. "in a    density" vs "in a density").
# If ALL differences are single-space vs multi-space at word boundaries,
# they are formatting artifacts only — not semantic changes.
import re

def normalize_whitespace(s: str) -> str:
    # Collapse runs of spaces (not newlines) to a single space
    return re.sub(r'[ \t]+', ' ', s)

if normalize_whitespace(py_prompt) == normalize_whitespace(md_body):
    print("\nNOTE: Differences are whitespace-only (Python line-continuation "
          "artifacts). Semantic content is identical. Safe to proceed.")
    sys.exit(0)
else:
    print("\nDifferences are NOT whitespace-only. Do not delete the Python "
          "constant until these are resolved.")
    sys.exit(1)
