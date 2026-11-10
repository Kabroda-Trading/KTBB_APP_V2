"""Unit coverage for cme_contracts.py -- the root -> dated NT8 instrument
resolver (with quarterly rollover) that feeds CrossTrade's GetBars RPC.
Pure functions, no network, no DB.

The 3rd-Friday rollover boundary is the one date-sensitive detail the
resolver's own header flags as "confirm against live NT8" -- these tests
pin the algorithm (quarter cycle, 3rd-Friday math, year rollover), not a
live-confirmed rollover instant."""
import datetime

import pytest

import cme_contracts as cc

UTC = datetime.timezone.utc


# ------------------------------------------------------------------ third_friday

def test_third_friday_known_dates():
    # 2026 quarter third-Fridays (independently verifiable calendar facts).
    assert cc.third_friday(2026, 3) == datetime.date(2026, 3, 20)
    assert cc.third_friday(2026, 6) == datetime.date(2026, 6, 19)
    assert cc.third_friday(2026, 9) == datetime.date(2026, 9, 18)
    assert cc.third_friday(2026, 12) == datetime.date(2026, 12, 18)


def test_third_friday_when_month_starts_on_friday():
    # A month whose 1st is a Friday has its 3rd Friday on the 15th.
    # 2027-01-01 is a Friday.
    assert cc.third_friday(2027, 1) == datetime.date(2027, 1, 15)


# ------------------------------------------------------------------ resolve_contract

def test_resolve_contract_mid_quarter_returns_current_front_month():
    # 2026-01-15 is mid-January -> front month is March (H).
    assert cc.resolve_contract("MNQ", datetime.datetime(2026, 1, 15, 15, 0, tzinfo=UTC)) == "MNQ 03-26"


def test_resolve_contract_day_before_expiry_still_current_quarter():
    # 3rd Friday of March 2026 is 03-20; on 03-19 the March contract is
    # still front (rollover is ON the 3rd Friday, not before).
    assert cc.resolve_contract("MNQ", datetime.datetime(2026, 3, 19, 15, 0, tzinfo=UTC)) == "MNQ 03-26"


def test_resolve_contract_on_expiry_rolls_to_next_quarter():
    # 03-20 is the 3rd Friday -> next quarter (June) is now front.
    assert cc.resolve_contract("MNQ", datetime.datetime(2026, 3, 20, 15, 0, tzinfo=UTC)) == "MNQ 06-26"


def test_resolve_contract_after_september_expiry_rolls_to_december():
    # September 3rd Friday 2026 is 09-18; on 09-20 December is front.
    assert cc.resolve_contract("MES", datetime.datetime(2026, 9, 20, 15, 0, tzinfo=UTC)) == "MES 12-26"


def test_resolve_contract_year_rollover_from_december():
    # December 3rd Friday 2026 is 12-18 -> next quarter is March 2027.
    assert cc.resolve_contract("ES", datetime.datetime(2026, 12, 18, 15, 0, tzinfo=UTC)) == "ES 03-27"


def test_resolve_contract_all_supported_roots():
    # Every root maps cleanly and keeps its own root prefix.
    dt = datetime.datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    assert cc.resolve_contract("MNQ", dt) == "MNQ 12-26"
    assert cc.resolve_contract("MES", dt) == "MES 12-26"
    assert cc.resolve_contract("NQ", dt) == "NQ 12-26"
    assert cc.resolve_contract("ES", dt) == "ES 12-26"


def test_resolve_contract_defaults_to_now():
    # No as_of passed -> uses now; must return a well-formed dated string
    # (root + space + MM-YY) regardless of today's date.
    out = cc.resolve_contract("MNQ")
    assert out.startswith("MNQ ")
    _, dated = out.split(" ")
    mm, yy = dated.split("-")
    assert 1 <= int(mm) <= 12 and int(mm) in (3, 6, 9, 12)
    assert 0 <= int(yy) <= 99


def test_resolve_contract_lowercase_root_normalized():
    assert cc.resolve_contract("mnq", datetime.datetime(2026, 11, 9, 15, 0, tzinfo=UTC)) == "MNQ 12-26"


def test_resolve_contract_unsupported_root_raises():
    with pytest.raises(ValueError, match="unsupported CME root"):
        cc.resolve_contract("GC", datetime.datetime(2026, 11, 9, 15, 0, tzinfo=UTC))


def test_resolve_contract_naive_datetime_treated_as_utc():
    # A naive datetime must not crash (coerced to UTC internally).
    assert cc.resolve_contract("MNQ", datetime.datetime(2026, 11, 9, 15, 0)) == "MNQ 12-26"
