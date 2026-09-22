"""
Unit coverage for market_data.fetch_bitunix_4h()/_bitunix_kline_page() --
2026-09-22, CC_INTERFACE.md item 3 (BBWP's own live feed, Bitunix). Mocks
the `_bitunix_kline_page` seam (matching this codebase's own established
convention of monkeypatching a named method/function, never raw aiohttp --
see tests/test_executor_bitunix_client.py's own house style), never makes a
real network call.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_market_data_bitunix_4h.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio

import pytest

import database
from database import SessionLocal, CandleHistory
import market_data as md


def _clean_db_files():
    for path in ["kabroda_test_market_data_bitunix_4h.db", "kabroda_test_market_data_bitunix_4h.db-journal",
                 "kabroda_test_market_data_bitunix_4h.db-shm", "kabroda_test_market_data_bitunix_4h.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def db():
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    session.query(CandleHistory).delete()
    session.commit()
    yield session
    session.query(CandleHistory).delete()
    session.commit()
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _run(coro):
    return asyncio.run(coro)


def _row(ms, close, high=None, low=None, base_vol="10.5"):
    # Real Bitunix rows are ALL string-typed, including time -- verified
    # live 2026-09-22. Fixtures mirror that exactly, not a convenient int.
    return {"time": str(ms), "open": str(close), "high": str(high if high is not None else close),
            "low": str(low if low is not None else close), "close": str(close), "baseVol": base_vol,
            "quoteVol": "1.0"}


def _page(monkeypatch, pages):
    """Installs a fake _bitunix_kline_page that returns pages in order,
    keyed by call sequence -- and records the endTime each call received,
    so tests can assert on the pagination boundary itself."""
    calls = []

    async def fake(session, symbol, interval, limit, end_time_ms=None):
        calls.append({"symbol": symbol, "interval": interval, "limit": limit, "end_time_ms": end_time_ms})
        idx = len(calls) - 1
        return pages[idx] if idx < len(pages) else []

    monkeypatch.setattr(md, "_bitunix_kline_page", fake)
    return calls


# ------------------------------------------------------------------ field mapping / type casting

def test_single_page_maps_fields_and_casts_string_types(db, monkeypatch):
    # Real rows are string-typed for EVERY field, including time -- this
    # is the exact bug class that would raise TypeError on the first real
    # poll if int()/float() casts were skipped.
    page = [_row(1_700_003_600_000, 105.0, high=106.0, low=104.0, base_vol="2.5"),
            _row(1_700_000_000_000, 100.0, high=101.0, low=99.0, base_vol="1.5")]
    _page(monkeypatch, [page, []])  # second page empty -> stop (short page rule also applies: len(page)<200)
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert [c["time"] for c in result] == [1_700_000_000, 1_700_003_600]  # ascending, ms->s
    assert result[0] == {"time": 1_700_000_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.5}
    assert all(isinstance(c["time"], int) for c in result)


# ------------------------------------------------------------------ pagination boundary

def test_pagination_walks_endtime_using_prior_pages_oldest_bar(db, monkeypatch):
    # page1 must be a FULL page (== page_size) or the "short page -> history
    # exhausted" rule stops pagination before ever reaching page 2.
    page1 = [_row(i * 1000, float(i)) for i in range(1000, 800, -1)]  # 200 rows, oldest = 801000ms
    page2 = [_row(500, 1.0)]                                          # short page -> stop after this
    calls = _page(monkeypatch, [page1, page2])
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert calls[0]["end_time_ms"] is None
    assert calls[1]["end_time_ms"] == 801000   # exactly page1's own oldest `time`
    assert len(calls) == 2                     # stopped after the short page, no third call
    assert result[0]["time"] == 0              # 500ms -> 0s, the oldest bar overall


def test_pagination_stops_at_target_bars_without_an_extra_call(db, monkeypatch):
    page_a = [_row(i * 1000, float(i)) for i in range(1000, 800, -1)]  # 200 rows, oldest=801000ms
    page_b = [_row(i * 1000, float(i)) for i in range(800, 600, -1)]   # 200 rows, oldest=601000ms, no overlap with page_a
    page_c = [_row(i * 1000, float(i)) for i in range(600, 400, -1)]   # would be a 3rd page if ever requested
    calls = _page(monkeypatch, [page_a, page_b, page_c])
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=400))
    assert len(calls) == 2          # 200 + 200 = 400 already meets target -- no 3rd page requested
    assert len(result) == 400


# ------------------------------------------------------------------ failure handling

def test_malformed_midpage_failure_keeps_the_partial_result(db, monkeypatch):
    # _bitunix_kline_page() itself returns [] on a non-zero code -- this
    # test proves fetch_bitunix_4h() keeps whatever was ALREADY assembled
    # rather than discarding it or raising. page1 must be a FULL page or
    # the short-page rule stops it before page 2 is ever requested.
    page1 = [_row(i * 1000, float(i)) for i in range(1000, 800, -1)]  # 200 rows
    calls = _page(monkeypatch, [page1, []])  # page 2 "fails" (empty from the mock's own code-check branch)
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert len(calls) == 2
    assert len(result) == 200  # page1's rows kept, not discarded


def test_empty_first_page_returns_empty_cleanly(db, monkeypatch):
    _page(monkeypatch, [[]])
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert result == []


def test_exception_on_a_page_degrades_to_the_partial_result(db, monkeypatch):
    page1 = [_row(2000, 3.0), _row(1000, 2.0)]
    calls_seen = {"n": 0}

    async def fake(session, symbol, interval, limit, end_time_ms=None):
        calls_seen["n"] += 1
        if calls_seen["n"] == 1:
            return page1
        raise RuntimeError("boom")

    monkeypatch.setattr(md, "_bitunix_kline_page", fake)
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert [c["time"] for c in result] == [1, 2]


def test_pagination_never_spins_if_endtime_fails_to_move_backward(db, monkeypatch):
    stuck_page = [_row(5000, 1.0)]  # always the same single bar, never moves backward
    calls = _page(monkeypatch, [stuck_page] * 10)
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert len(calls) == 1   # the short-page rule (1 row < 200-row page size) already stops it
    assert [c["time"] for c in result] == [5]


def test_pagination_safety_guard_even_with_a_full_page_stuck_at_the_same_endtime(db, monkeypatch):
    # A pathological mock that returns a full-size page every time but never
    # actually moves its own oldest timestamp backward -- proves the
    # explicit end_time_ms-must-decrease guard, independent of the
    # short-page early-exit exercised above.
    stuck_full_page = [_row(5000, float(i)) for i in range(200)]
    calls = _page(monkeypatch, [stuck_full_page] * 10)
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert len(calls) == 2   # 1st call, then the 2nd call's oldest ties end_time_ms -> guard stops it
    assert len(result) == 1  # all 200 rows share the same time (5000) -> deduped to one candle


# ------------------------------------------------------------------ symbol normalization + persistence

def test_symbol_normalized_for_the_outbound_request(db, monkeypatch):
    calls = _page(monkeypatch, [[_row(1000, 100.0)]])
    _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert calls[0]["symbol"] == "BTCUSDT"


def test_persists_under_a_distinct_bitunix_timeframe_label_with_the_slash_symbol(db):
    async def fake(session, symbol, interval, limit, end_time_ms=None):
        return [_row(1000, 100.0)] if end_time_ms is None else []
    import market_data as md2

    async def run():
        orig = md2._bitunix_kline_page
        md2._bitunix_kline_page = fake
        try:
            return await md2.fetch_bitunix_4h("BTC/USDT", target_bars=900)
        finally:
            md2._bitunix_kline_page = orig

    _run(run())
    row = db.query(CandleHistory).filter_by(timeframe="4H_BITUNIX").one()
    assert row.symbol == "BTC/USDT"   # slash form, matching every other persisted row for this symbol
    assert row.close == 100.0


def test_below_minimum_bars_still_returns_what_was_fetched(db, monkeypatch, capsys):
    _page(monkeypatch, [[_row(1000, 100.0)]])  # far short of BITUNIX_BBWP_MIN_BARS
    result = _run(md.fetch_bitunix_4h("BTC/USDT", target_bars=900))
    assert len(result) == 1
    assert "BBWP will stay undefined" in capsys.readouterr().out
