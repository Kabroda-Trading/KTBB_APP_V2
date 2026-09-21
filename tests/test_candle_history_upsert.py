"""
market_data._persist_candles() -- 2026-09-21 audit. The insert-only version
stored each bar as first seen (mid-bar for a live poll) and never refreshed
it, so candle_history closes were first-sight snapshots and audit
reproductions built from them were unreliable (AGENT_LOG 2026-09-21).
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_candle_history_upsert.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime

import pytest

import database
from database import SessionLocal, CandleHistory
import market_data as md

T0 = 1_800_000_000 - (1_800_000_000 % 300)


def _clean_db_files():
    for path in ["kabroda_test_candle_history_upsert.db", "kabroda_test_candle_history_upsert.db-journal",
                 "kabroda_test_candle_history_upsert.db-shm", "kabroda_test_candle_history_upsert.db-wal"]:
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


def _bar(i, close, high=None, low=None, volume=10.0):
    return {"time": T0 + i * 300, "open": 100.0, "high": high if high is not None else close,
            "low": low if low is not None else close, "close": close, "volume": volume}


def _stored(db):
    db.expire_all()
    return {row.timestamp: row for row in db.query(CandleHistory).filter_by(symbol="BTC/USDT", timeframe="5M").all()}


def _ts(i):
    return datetime.datetime.utcfromtimestamp(T0 + i * 300)


def test_first_sight_inserts_the_bar(db):
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 101.0)])
    assert _stored(db)[_ts(0)].close == 101.0


def test_a_forming_bars_snapshot_is_refreshed_to_its_final_ohlcv(db):
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 100.4, high=100.6, low=99.9, volume=1.0)])   # first seen, mid-bar
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 105.0, high=106.0, low=99.0, volume=42.0)])  # later fetch, final values
    row = _stored(db)[_ts(0)]
    assert (row.close, row.high, row.low, row.volume) == (105.0, 106.0, 99.0, 42.0)
    assert len(_stored(db)) == 1   # updated in place, not duplicated


def test_unchanged_refetch_is_a_no_op(db):
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 101.0)])
    first_created = _stored(db)[_ts(0)].created_at
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 101.0)])
    rows = _stored(db)
    assert len(rows) == 1 and rows[_ts(0)].close == 101.0 and rows[_ts(0)].created_at == first_created


def test_stale_early_rows_inside_the_fetched_window_self_heal_and_new_bars_still_insert(db):
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 100.1), _bar(1, 100.2), _bar(2, 100.3)])   # all stored at first sight
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 101.0), _bar(1, 102.0), _bar(2, 103.0), _bar(3, 104.0)])
    rows = _stored(db)
    assert [rows[_ts(i)].close for i in range(4)] == [101.0, 102.0, 103.0, 104.0]


def test_symbol_and_timeframe_are_not_cross_contaminated(db):
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 101.0)])
    md._persist_candles("BTC/USDT", "1H", [_bar(0, 555.0)])
    md._persist_candles("ETH/USDT", "5M", [_bar(0, 777.0)])
    md._persist_candles("BTC/USDT", "5M", [_bar(0, 102.0)])
    assert _stored(db)[_ts(0)].close == 102.0
    other = {(r.symbol, r.timeframe): r.close for r in db.query(CandleHistory).all()}
    assert other[("BTC/USDT", "1H")] == 555.0 and other[("ETH/USDT", "5M")] == 777.0
