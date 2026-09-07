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


# ------------------------------------------------------------------ resting reduce-only LIMIT at T1 (2026-09-06)
# Same bug class this harness exists to catch (dropped/mismatched
# onclick arguments), new surface area of it.

def test_place_resting_t1_limit_calls_the_right_endpoint():
    scenarios, _ = _run_harness()
    place = next(s for s in scenarios if s["label"] == "placeRestingT1Limit")
    assert place["ok"] is True
    assert any(p.endswith("/tiny-test/5/place-resting-t1-limit") for p in place["fetchCalls"])


def test_check_resting_t1_limit_status_calls_the_right_endpoint():
    scenarios, _ = _run_harness()
    check = next(s for s in scenarios if s["label"] == "checkRestingT1LimitStatus")
    assert check["ok"] is True
    assert any(p.endswith("/tiny-test/5/check-resting-t1-limit-status") for p in check["fetchCalls"])


def test_cancel_resting_t1_limit_calls_the_right_endpoint():
    scenarios, _ = _run_harness()
    cancel = next(s for s in scenarios if s["label"] == "cancelRestingT1Limit")
    assert cancel["ok"] is True
    assert any(p.endswith("/tiny-test/5/cancel-resting-t1-limit") for p in cancel["fetchCalls"])


# ------------------------------------------------------------------ Go Live mode switch (2026-09-07)
# Two mutually-exclusive onclick call sites for the same function name --
# the harness's findOnclickAttrForLiteralArg() disambiguates by the
# literal 'LIVE'/'DRY_RUN' argument. Confirms both wire to the real route
# with the right mode in the request body, not just that they don't throw.

def test_go_live_button_calls_the_mode_endpoint_with_live():
    scenarios, returncode = _run_harness()
    go_live = next(s for s in scenarios if s["label"] == "setAccountMode(LIVE)")
    assert go_live["ok"] is True, go_live.get("error")
    assert any(p.endswith("/accounts/1/mode") for p in go_live["fetchCalls"])


def test_revert_to_dry_run_button_calls_the_mode_endpoint_with_dry_run():
    scenarios, _ = _run_harness()
    revert = next(s for s in scenarios if s["label"] == "setAccountMode(DRY_RUN)")
    assert revert["ok"] is True, revert.get("error")
    assert any(p.endswith("/accounts/1/mode") for p in revert["fetchCalls"])


# ------------------------------------------------------------------ _parseServerTimestamp (2026-09-06)
# Real bug Andy caught live: naive-UTC timestamps (Python's .isoformat()
# on a datetime.utcnow()-based column, no "Z"/offset) were displayed as
# if already in the viewer's local timezone.

def test_parse_server_timestamp_appends_z_to_naive_utc_strings():
    scenarios, returncode = _run_harness()
    naive = next(s for s in scenarios if s["label"] == "_parseServerTimestamp: naive UTC string gets Z appended")
    assert naive["ok"] is True
    assert returncode == 0


def test_parse_server_timestamp_does_not_double_append_an_existing_zone():
    scenarios, _ = _run_harness()
    already_z = next(s for s in scenarios if s["label"] == "_parseServerTimestamp: already-Z string not double-appended")
    with_offset = next(s for s in scenarios if s["label"] == "_parseServerTimestamp: explicit-offset string not double-appended")
    assert already_z["ok"] is True
    assert with_offset["ok"] is True


def test_parse_server_timestamp_handles_null():
    scenarios, _ = _run_harness()
    null_case = next(s for s in scenarios if s["label"] == "_parseServerTimestamp: null input")
    assert null_case["ok"] is True
