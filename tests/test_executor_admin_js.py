"""
Executes (not just reads) templates/executor_admin.html's inline <script>
block via Node, for the four real-money tiny-test action buttons. This
repo's Python test suite is otherwise thorough but has zero ability to
run frontend JS -- which is exactly how a real bug shipped and reached
production undetected (2026-09-05): the click-guard helper added to
placeTinyTest()/partialCloseTinyTest()/moveSlBreakevenTinyTest()/
flashCloseTinyTest() required the button element as an argument, but
the onclick="" attributes calling them were never updated to pass it.
Every click threw a TypeError before ever reaching fetch() -- silent in
the browser, invisible in server logs (no request was ever sent), and
completely outside what any Python-side test could have caught.

Skips cleanly (not a failure) if Node.js isn't installed -- this repo
has no other Node dependency, and this check is a real bonus, not a
hard requirement to run the rest of the suite.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

_HARNESS_PATH = os.path.join(os.path.dirname(__file__), "executor_admin_js_harness.js")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js not installed -- this is the only test in the suite that needs it",
)


def _run_harness():
    result = subprocess.run(
        ["node", _HARNESS_PATH], capture_output=True, text=True, timeout=30,
    )
    try:
        scenarios = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"harness produced non-JSON output (exit {result.returncode}):\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    return scenarios, result.returncode


def test_all_tiny_test_buttons_execute_without_throwing():
    scenarios, returncode = _run_harness()
    failures = [s for s in scenarios if not s.get("ok")]
    assert not failures, f"{len(failures)} tiny-test button handler(s) failed: {json.dumps(failures, indent=2)}"
    assert returncode == 0


def test_place_tiny_test_calls_the_place_endpoint():
    scenarios, _ = _run_harness()
    place = next(s for s in scenarios if s["label"] == "placeTinyTest")
    assert place["ok"] is True
    assert any(p.endswith("/tiny-test/place") for p in place["fetchCalls"])


def test_partial_close_calls_the_partial_close_endpoint_with_the_right_test_id():
    scenarios, _ = _run_harness()
    partial = next(s for s in scenarios if s["label"] == "partialCloseTinyTest")
    assert partial["ok"] is True
    assert any(p.endswith("/tiny-test/5/partial-close") for p in partial["fetchCalls"])


def test_move_sl_breakeven_calls_the_right_endpoint():
    scenarios, _ = _run_harness()
    breakeven = next(s for s in scenarios if s["label"] == "moveSlBreakevenTinyTest")
    assert breakeven["ok"] is True
    assert any(p.endswith("/tiny-test/5/move-sl-breakeven") for p in breakeven["fetchCalls"])


def test_flash_close_calls_the_right_endpoint():
    scenarios, _ = _run_harness()
    flash = next(s for s in scenarios if s["label"] == "flashCloseTinyTest")
    assert flash["ok"] is True
    assert any(p.endswith("/tiny-test/5/flash-close") for p in flash["fetchCalls"])
