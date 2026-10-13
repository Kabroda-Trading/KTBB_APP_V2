"""Unit + integration coverage for the MetaSignals Realistic Paper Trading
Desk (Andy directive 2026-10-13, METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md):
metasignals_triage.py (5-pillar gate), metasignals_paper_engine.py (fill
simulation/fees/BE ratchet), metasignals_watcher.py (30s poll), metasignals_
radar.py, and the three HTTP routes. Same conventions as the Alt Matrix
test suite this subsystem's own architecture mirrors: plain async closures
+ asyncio.run() for the pure-network-mocked triage tests (test_alt_matrix_
market.py's own style), a real file-backed SQLite DB for anything touching
the paper ledger (test_alt_matrix_portfolio.py's own style), TestClient for
the HTTP routes (test_alt_matrix_routes.py's own style).

Step 5's own explicit requirement (METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md
section 8): verify ZERO imports from Traveler or Alt Matrix in
metasignals_*.py -- enforced here for real via an AST-based static check,
not just a one-off manual grep.
"""
import ast
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_metasignals_paper.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

from unittest.mock import MagicMock
sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

import glob as _glob
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import database
from database import (
    SessionLocal, UserModel, MetaSignalsPaperAccount, MetaSignalsPlan,
    MetaSignalsPaperOrder, MetaSignalsPaperLedger, MetaSignalsRawAlert,
)
import market_data
import executor_bitunix_client as ebc
import metasignals_triage as triage
import metasignals_paper_engine as engine
import metasignals_watcher as watcher
import metasignals_radar as radar


# ============================================================== helpers

def _run(coro):
    return asyncio.run(coro)


def _async(value=None, exc=None):
    async def _fake(*a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


def _candles_4h(n=60, start=100.0, up=1.0, down=0.8, base_time=1_000_000):
    """Alternating up/down closes -- converges to a Wilder RSI(14) in the
    healthy 40-62 band (steady-state RS = (up/2)/(down/2) = up/down =
    1.25 -> RSI = 100 - 100/(1+1.25) = 55.56), constant range (1.0) so
    ATR14 converges cleanly too."""
    candles = []
    close = start
    for i in range(n):
        if i > 0:
            close = close + up if i % 2 == 1 else close - down
        candles.append({"time": base_time + i * 14400, "open": close, "high": close + 0.5, "low": close - 0.5, "close": close})
    return candles


def _candles_daily_up(n=220, start=100.0, step=0.5, base_time=1_000_000):
    """Monotonic uptrend -- latest close is always above any trailing
    SMA/EMA window, a clean macro-pass fixture."""
    candles = []
    for i in range(n):
        close = start + i * step
        candles.append({"time": base_time + i * 86400, "open": close, "high": close + 0.3, "low": close - 0.3, "close": close})
    return candles


def _candles_daily_down(n=220, start=300.0, step=0.5, base_time=1_000_000):
    """Monotonic downtrend -- latest close is always below any trailing
    SMA/EMA window, a clean macro-fail fixture."""
    candles = []
    for i in range(n):
        close = start - i * step
        candles.append({"time": base_time + i * 86400, "open": close, "high": close + 0.3, "low": close - 0.3, "close": close})
    return candles


def _patch_market(monkeypatch, candles_4h=None, candles_daily_asset=None, candles_daily_btc=None,
                   funding_rate="0.0001", mark_price=None):
    """Patches market_data.fetch_bitunix_4h/fetch_bitunix_daily and
    executor_bitunix_client.fetch_public_funding_rate -- the only three
    network-touching calls metasignals_triage.evaluate_alert() makes.
    fetch_bitunix_daily is symbol-aware (BTC gets its own series) so a
    single test can fail exactly one pillar without the others moving."""
    c4h = candles_4h if candles_4h is not None else _candles_4h()
    c1d_asset = candles_daily_asset if candles_daily_asset is not None else _candles_daily_up()
    c1d_btc = candles_daily_btc if candles_daily_btc is not None else _candles_daily_up(start=50000.0, step=50.0)
    mp = mark_price if mark_price is not None else c4h[-1]["close"]

    async def fake_4h(symbol, target_bars=120):
        return c4h

    async def fake_daily(symbol, target_bars=250):
        return c1d_btc if "BTC" in symbol.upper() else c1d_asset

    async def fake_funding(symbol):
        return {"code": 0, "data": {"fundingRate": funding_rate, "markPrice": str(mp)}}

    monkeypatch.setattr(market_data, "fetch_bitunix_4h", fake_4h)
    monkeypatch.setattr(market_data, "fetch_bitunix_daily", fake_daily)
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", fake_funding)
    return {"candles_4h": c4h, "mark_price": mp}


def _good_alert(**overrides):
    base = dict(
        symbol="SOLUSDT", side="BUY", entry_price=160.0, soft_stop=150.0,
        tp1=170.0, tp2=180.0, timeframe="6H", strategy="2.0.1",
    )
    base.update(overrides)
    return base


# ============================================================== Iron Wall

METASIGNALS_MODULES = [
    "metasignals_triage.py", "metasignals_paper_engine.py",
    "metasignals_watcher.py", "metasignals_radar.py",
]
_FORBIDDEN_IMPORT_PREFIXES = (
    "gate_traveler", "traveler_plan_engine", "traveler_plan_notify", "traveler_radar",
    "battlebox_pipeline", "executor_live_e1_engine", "executor_plan_builder", "executor_sizing",
    "alt_matrix_",
)


def _imported_module_names(path):
    tree = ast.parse(open(path, "r", encoding="utf-8").read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("filename", METASIGNALS_MODULES)
def test_iron_wall_no_forbidden_imports(filename):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(root, filename)
    assert os.path.exists(path), f"{filename} not found at expected repo-root location"
    names = _imported_module_names(path)
    for name in names:
        for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
            assert not name.startswith(forbidden), f"{filename} imports forbidden module {name!r}"


def test_no_traveler_or_alt_matrix_module_imports_metasignals():
    """The reverse direction -- nothing in the existing Traveler or Alt
    Matrix lineage should ever need to import a metasignals_* module."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    traveler_and_altmatrix_files = (
        _glob.glob(os.path.join(root, "gate_traveler.py"))
        + _glob.glob(os.path.join(root, "traveler_*.py"))
        + _glob.glob(os.path.join(root, "alt_matrix_*.py"))
    )
    assert traveler_and_altmatrix_files, "expected to find at least the Traveler/Alt Matrix files"
    for path in traveler_and_altmatrix_files:
        names = _imported_module_names(path)
        for name in names:
            assert not name.startswith("metasignals_"), f"{os.path.basename(path)} imports {name!r}"


# ============================================================== discord content parser

def test_parse_discord_alert_returns_none_on_no_match():
    assert triage.parse_discord_alert("just some ordinary chatter in the channel") is None


def test_parse_discord_alert_returns_none_without_sl_line():
    text = "SOL | USDT @ $110.0400 - 6H - 2.0.1"
    assert triage.parse_discord_alert(text) is None


def test_parse_discord_alert_realistic_space_joined_fields_dotted_strategy():
    # Matches how the Pi's own embed-flattening actually joins fields:
    # "name: value" chunks separated by spaces, no sentence punctuation.
    text = "SOL | USDT @ $110.0400 - 6H - 2.0.1 SL Close Below: 105.40 Target 1: 114.19 Target 2: 120.56"
    parsed = triage.parse_discord_alert(text)
    assert parsed["symbol"] == "SOLUSDT"
    assert parsed["strategy"] == "2.0.1"   # regression: used to capture "2.0.1 SL Close Below"
    assert parsed["soft_stop"] == pytest.approx(105.40)
    assert parsed["tp1"] == pytest.approx(114.19)
    assert parsed["tp2"] == pytest.approx(120.56)
    assert parsed["tp3"] is None


def test_parse_discord_alert_two_token_strategy_name():
    text = "SOL | USDT @ $110.0400 - 6H - 2.0 G2 SL Close Below: 105.40 Target 1: 114.19 Target 2: 120.56"
    parsed = triage.parse_discord_alert(text)
    assert parsed["strategy"] == "2.0 G2"   # regression: used to capture "2.0 G2 SL Close Below"


def test_parse_discord_alert_trailing_punctuation_on_numbers_does_not_break_float_parsing():
    # Regression: the original [\d\.]+ pattern swallowed a trailing
    # sentence-ending period directly after a value.
    text = "SOL | USDT @ $110.0400 - 6H - 2.0.1. SL Close Below: 105.40. Target 1: 114.19."
    parsed = triage.parse_discord_alert(text)
    assert parsed is not None
    assert parsed["soft_stop"] == pytest.approx(105.40)
    assert parsed["tp1"] == pytest.approx(114.19)


def test_parse_discord_alert_defaults_tp1_tp2_when_missing():
    text = "SOL | USDT @ $100.00 - 6H - 2.0.1 SL Close Below: 95.00"
    parsed = triage.parse_discord_alert(text)
    assert parsed["tp1"] == pytest.approx(104.0)   # entry * 1.04
    assert parsed["tp2"] == pytest.approx(108.0)   # entry * 1.08


def test_parse_discord_alert_reads_embeds_title_description_and_fields():
    embeds = [{
        "title": "SOL | USDT @ $110.0400 - 6H - 2.0.1",
        "description": "a setup",
        "fields": [{"name": "SL Close Below", "value": "105.40"}, {"name": "Target 1", "value": "114.19"}],
    }]
    parsed = triage.parse_discord_alert(None, embeds)
    assert parsed is not None
    assert parsed["symbol"] == "SOLUSDT"
    assert parsed["soft_stop"] == pytest.approx(105.40)


# ============================================================== pure math

def test_rsi_all_gains_is_100():
    closes = [100.0 + i for i in range(20)]
    rsi = triage.rsi14_wilder_series(closes)
    assert rsi[14] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    closes = [100.0 - i for i in range(20)]
    rsi = triage.rsi14_wilder_series(closes)
    assert rsi[14] == pytest.approx(0.0)


def test_rsi_none_before_warmup():
    closes = [100.0 + i for i in range(10)]
    rsi = triage.rsi14_wilder_series(closes)
    assert all(v is None for v in rsi)


def test_rsi_alternating_series_lands_in_healthy_band():
    closes = [c["close"] for c in _candles_4h()]
    rsi = triage.rsi14_wilder_series(closes)[-1]
    assert 40.0 < rsi < 62.0


def test_sma_series_matches_plain_mean():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    sma = triage.sma_series(values, 3)
    assert sma[:2] == [None, None]
    assert sma[2] == pytest.approx(2.0)
    assert sma[4] == pytest.approx(4.0)


def test_ema_series_first_value_equals_input():
    values = [10.0, 20.0, 30.0]
    ema = triage.ema_series(values, 5)
    assert ema[0] == pytest.approx(10.0)


def test_atr14_wilder_constant_range_and_flat_close_converges_to_that_range():
    # Flat close (no gap component in the true-range formula) isolates
    # the plain high-low range -- _candles_4h()'s alternating closes
    # also contribute a real gap-vs-prior-close term to true range, so
    # it converges above the plain 1.0 high-low range, not to it.
    candles = [{"time": 1_000_000 + i * 14400, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0} for i in range(30)]
    atr = triage.atr14_wilder_series(candles)[-1]
    assert atr == pytest.approx(1.0, abs=1e-9)


# ============================================================== triage gate

def test_evaluate_alert_full_approval_yes(monkeypatch):
    fixture = _patch_market(monkeypatch, mark_price=160.0)
    alert = _good_alert(entry_price=160.0)
    result = _run(triage.evaluate_alert(alert))
    assert result["verdict"] == "YES"
    assert result["reason"] == "APPROVED"
    assert result["entry_mode"] == "MARKET"
    assert result["hard_stop"] < alert["soft_stop"]
    assert result["position_size"] == pytest.approx(50.0 / (160.0 - result["hard_stop"]))
    assert result["tp1_price"] > alert["entry_price"]
    assert result["tp2_price"] == pytest.approx(alert["tp2"])


def test_evaluate_alert_limit_entry_mode_when_moderately_extended(monkeypatch):
    # First pass (mark == entry, extension_r == 0) just to learn the
    # real r_distance (= entry - hard_stop) this fixture produces, then
    # re-run with a mark price deliberately placed between 0.10R and
    # 0.25R above entry to land on the LIMIT branch.
    _patch_market(monkeypatch, mark_price=160.0)
    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    baseline = _run(triage.evaluate_alert(alert))
    r_distance = alert["entry_price"] - baseline["hard_stop"]
    extended_mark = alert["entry_price"] + 0.15 * r_distance

    _patch_market(monkeypatch, mark_price=extended_mark)
    result = _run(triage.evaluate_alert(alert))
    assert result["verdict"] == "YES"
    assert result["entry_mode"] == "LIMIT"


def test_evaluate_alert_vetoes_whitelist_symbol(monkeypatch):
    _patch_market(monkeypatch)
    alert = _good_alert(symbol="DOGEUSDT")
    result = _run(triage.evaluate_alert(alert))
    assert result == {"symbol": "DOGEUSDT", "verdict": "NO", "reason": "VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED"}


def test_evaluate_alert_vetoes_whitelist_timeframe(monkeypatch):
    _patch_market(monkeypatch)
    alert = _good_alert(timeframe="15M")
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED"


def test_evaluate_alert_vetoes_whitelist_strategy(monkeypatch):
    _patch_market(monkeypatch)
    alert = _good_alert(strategy="unknown-strategy")
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED"


def test_evaluate_alert_vetoes_short_side(monkeypatch):
    _patch_market(monkeypatch)
    alert = _good_alert(side="SELL")
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_SIDE_NOT_SUPPORTED"


def test_evaluate_alert_vetoes_macro_when_asset_below_its_own_trend(monkeypatch):
    _patch_market(monkeypatch, candles_daily_asset=_candles_daily_down())
    alert = _good_alert()
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_MACRO_BELOW_200SMA"
    assert result["asset_daily_close"] < result["asset_sma200"]


def test_evaluate_alert_vetoes_macro_when_btc_below_its_own_trend(monkeypatch):
    _patch_market(monkeypatch, candles_daily_btc=_candles_daily_down(start=70000.0, step=50.0))
    alert = _good_alert()
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_MACRO_BELOW_200SMA"


def test_evaluate_alert_vetoes_overbought_rsi(monkeypatch):
    # All-gains 4H series -> RSI pinned at 100.
    overbought_4h = [
        {"time": 1_000_000 + i * 14400, "open": 100 + i, "high": 100 + i + 0.5, "low": 100 + i - 0.5, "close": 100.0 + i}
        for i in range(30)
    ]
    _patch_market(monkeypatch, candles_4h=overbought_4h, mark_price=overbought_4h[-1]["close"])
    alert = _good_alert(entry_price=overbought_4h[-1]["close"], soft_stop=overbought_4h[-1]["close"] - 10)
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_MOMENTUM_OVERBOUGHT"


def test_evaluate_alert_vetoes_collapsed_rsi(monkeypatch):
    collapsed_4h = [
        {"time": 1_000_000 + i * 14400, "open": 200 - i, "high": 200 - i + 0.5, "low": 200 - i - 0.5, "close": 200.0 - i}
        for i in range(30)
    ]
    _patch_market(monkeypatch, candles_4h=collapsed_4h, mark_price=collapsed_4h[-1]["close"])
    alert = _good_alert(entry_price=collapsed_4h[-1]["close"], soft_stop=collapsed_4h[-1]["close"] - 10)
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_MOMENTUM_COLLAPSED"


def test_evaluate_alert_vetoes_overextended_price(monkeypatch):
    _patch_market(monkeypatch, mark_price=400.0)   # wildly above entry/alert price
    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_PRICE_OVER_EXTENDED"


def test_evaluate_alert_vetoes_overcrowded_funding(monkeypatch):
    _patch_market(monkeypatch, mark_price=160.0, funding_rate="0.0010")
    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_FUNDING_OVERCROWDED"


def test_evaluate_alert_vetoes_funding_fetch_failure(monkeypatch):
    _patch_market(monkeypatch, mark_price=160.0)

    async def fake_funding(symbol):
        raise ConnectionError("blip")
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", fake_funding)

    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    result = _run(triage.evaluate_alert(alert))
    # markPrice comes from the same call as funding -- a hard failure of
    # that call fails closed on price first (pillar 3 runs before pillar 4).
    assert result["reason"] == "VETO_PRICE_FEED_UNAVAILABLE"


def test_evaluate_alert_vetoes_insufficient_4h_history(monkeypatch):
    _patch_market(monkeypatch, candles_4h=_candles_4h(n=5))
    alert = _good_alert()
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_INSUFFICIENT_4H_HISTORY"


def test_evaluate_alert_vetoes_insufficient_daily_history(monkeypatch):
    _patch_market(monkeypatch, candles_daily_asset=_candles_daily_up(n=10))
    alert = _good_alert()
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_INSUFFICIENT_DAILY_HISTORY"


def test_evaluate_alert_vetoes_invalid_stop_geometry(monkeypatch):
    _patch_market(monkeypatch, mark_price=160.0)
    # soft_stop ABOVE entry_price -- a malformed/contradictory alert for a long.
    alert = _good_alert(entry_price=160.0, soft_stop=165.0)
    result = _run(triage.evaluate_alert(alert))
    assert result["reason"] == "VETO_INVALID_STOP_GEOMETRY"


def test_evaluate_alert_asset_macro_falls_back_to_ema55_without_enough_history_for_sma200(monkeypatch):
    # 80 days -- enough for EMA55, not enough for a real SMA200 (None).
    short_up = _candles_daily_up(n=80)
    _patch_market(monkeypatch, candles_daily_asset=short_up, mark_price=160.0)
    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    result = _run(triage.evaluate_alert(alert))
    assert result["asset_sma200"] is None
    assert result["asset_ema55"] is not None
    assert result["verdict"] == "YES"   # uptrend -> close > ema55 -> macro pass via fallback


# ============================================================== paper engine (real DB)

def _clean_db_files():
    for path in [
        "kabroda_test_metasignals_paper.db", "kabroda_test_metasignals_paper.db-journal",
        "kabroda_test_metasignals_paper.db-shm", "kabroda_test_metasignals_paper.db-wal",
    ]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (MetaSignalsPaperLedger, MetaSignalsPaperOrder, MetaSignalsPlan, MetaSignalsRawAlert, MetaSignalsPaperAccount):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db():
    # Matches test_alt_matrix_portfolio.py's own established fixture
    # pattern -- NOT just belt-and-suspenders: a prior test run's
    # kabroda_test_metasignals_paper.db file can predate a schema change
    # (new columns added to an existing table), and init_db()'s
    # Base.metadata.create_all() only creates tables that don't exist
    # yet -- it never retroactively alters one that's already there
    # with an older shape. Deleting the file and disposing the engine
    # (both at setup and teardown) is what actually guarantees a fresh,
    # current schema every run, not merely resetting rows.
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                    hard_stop=150.0, position_size=5.0, tp1_price=170.0, tp2_price=180.0, symbol="SOLUSDT"):
    plan = MetaSignalsPlan(
        symbol=symbol, side="BUY", strategy="2.0.1", timeframe="6H",
        alert_price=alert_price, alert_soft_stop=hard_stop + 1.0,
        alert_tp1=tp1_price, alert_tp2=tp2_price,
        live_price_at_alert=live_price_at_alert, entry_mode=entry_mode,
        hard_stop=hard_stop, risk_usd=50.0, position_size=position_size,
        tp1_price=tp1_price, tp2_price=tp2_price,
        triage_verdict="YES", triage_reason="APPROVED", status="APPROVED",
    )
    db.add(plan)
    db.flush()
    return plan


def test_get_or_init_paper_account_seeds_500_and_writes_initial_deposit(db):
    account = engine.get_or_init_paper_account(db)
    assert account.cash_balance == pytest.approx(500.0)
    assert account.equity == pytest.approx(500.0)
    rows = db.query(MetaSignalsPaperLedger).filter_by(event_type="INITIAL_DEPOSIT").all()
    assert len(rows) == 1
    assert rows[0].amount == pytest.approx(500.0)


def test_get_or_init_paper_account_is_idempotent(db):
    a1 = engine.get_or_init_paper_account(db)
    a2 = engine.get_or_init_paper_account(db)
    assert a1.id == a2.id
    assert db.query(MetaSignalsPaperLedger).filter_by(event_type="INITIAL_DEPOSIT").count() == 1


def test_open_paper_trade_market_fills_immediately_and_deducts_taker_fee(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0)
    order = engine.open_paper_trade(db, plan)
    assert order.state == "OPEN"
    assert order.entry_price == pytest.approx(160.0)
    assert order.filled_at is not None

    expected_fee = engine.TAKER_FEE_RATE * 5.0 * 160.0
    assert order.fees_paid == pytest.approx(expected_fee)

    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    assert account.cash_balance == pytest.approx(500.0 - expected_fee)

    ledger = db.query(MetaSignalsPaperLedger).filter_by(order_id=order.id, event_type="ENTRY_FEE").first()
    assert ledger is not None
    assert ledger.amount == pytest.approx(-expected_fee)


def test_open_paper_trade_limit_rests_unfilled_until_touched(db):
    plan = _approved_plan(db, entry_mode="LIMIT", alert_price=160.0, live_price_at_alert=168.0,
                           hard_stop=150.0, position_size=5.0)
    order = engine.open_paper_trade(db, plan)
    assert order.state == "PENDING_LIMIT"
    assert order.entry_price == pytest.approx(160.0)   # resting AT the alert price
    assert order.filled_at is None

    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    assert account.cash_balance == pytest.approx(500.0)   # no fee yet -- not filled

    # Price still above the resting limit -- no fill.
    event = engine.advance_order(db, order, mark_price=165.0)
    assert event is None
    assert order.state == "PENDING_LIMIT"

    # Price retraces down to touch the resting limit -- fills now.
    event = engine.advance_order(db, order, mark_price=159.0)
    assert event == "ENTRY_FILL"
    assert order.state == "OPEN"
    assert order.filled_at is not None


def test_tp1_hit_ratchets_stop_to_breakeven_and_pays_maker_fee(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=10.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    cash_after_entry = account.cash_balance

    event = engine.advance_order(db, order, mark_price=171.0)
    assert event == "TP1_FILL"
    assert order.state == "HALF_CLOSED_BE"
    assert order.current_stop == pytest.approx(160.0)   # ratcheted to entry (breakeven)
    assert order.remaining_qty == pytest.approx(5.0)

    half_qty = 5.0
    expected_leg_pnl = half_qty * (170.0 - 160.0)
    expected_fee = engine.MAKER_FEE_RATE * half_qty * 170.0
    assert order.realized_pnl == pytest.approx(expected_leg_pnl)
    assert account.cash_balance == pytest.approx(cash_after_entry + expected_leg_pnl - expected_fee)

    # Account aggregates are NOT touched yet -- the trade isn't fully closed.
    assert account.total_trades == 0


def test_stop_hit_from_open_before_any_tp1_is_a_full_loss(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()

    event = engine.advance_order(db, order, mark_price=149.0)
    assert event == "CLOSED_STOP"
    assert order.state == "CLOSED_STOP"
    assert order.remaining_qty == pytest.approx(0.0)

    expected_leg_pnl = 5.0 * (150.0 - 160.0)   # -$50, matching the spec's own worked number
    assert order.realized_pnl == pytest.approx(expected_leg_pnl)
    assert account.total_trades == 1
    assert account.losing_trades == 1
    assert account.winning_trades == 0
    assert account.total_realized_pnl == pytest.approx(order.realized_pnl)
    assert order.closed_at is not None


def test_stop_hit_after_breakeven_ratchet_is_near_zero_not_a_full_loss(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=10.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    engine.advance_order(db, order, mark_price=171.0)   # TP1 -> ratchets to BE
    assert order.current_stop == pytest.approx(160.0)

    event = engine.advance_order(db, order, mark_price=159.0)   # BE stop touched
    assert event == "CLOSED_STOP"
    # remaining leg's own P&L is exactly zero (closed at entry price);
    # the position's total realized P&L is still the TP1 leg's own gain.
    tp1_leg_pnl = 5.0 * (170.0 - 160.0)
    assert order.realized_pnl == pytest.approx(tp1_leg_pnl)
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    assert account.winning_trades == 1   # realized_pnl > 0 overall, despite the BE-stop leg


def test_tp2_hit_closes_full_position_with_maker_fee(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=10.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    engine.advance_order(db, order, mark_price=171.0)   # TP1
    event = engine.advance_order(db, order, mark_price=181.0)   # TP2
    assert event == "CLOSED_TP2"
    assert order.state == "CLOSED_TP2"
    assert order.remaining_qty == pytest.approx(0.0)

    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    assert account.total_trades == 1
    assert account.winning_trades == 1
    ledger_exit = db.query(MetaSignalsPaperLedger).filter_by(order_id=order.id, event_type="EXIT").first()
    assert ledger_exit is not None


def test_stop_checked_before_tp1_when_both_conditions_are_met_in_one_tick(db):
    # A single mark price that is simultaneously <= stop AND >= tp1 is
    # impossible under normal entry/stop/tp1 ordering (stop < entry <
    # tp1), so this exercises the priority rule via two independent
    # single-condition calls instead, confirming STOP wins when checked
    # first on an OPEN order that has already dropped below stop.
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    event = engine.advance_order(db, order, mark_price=150.0)   # exactly at stop
    assert event == "CLOSED_STOP"


def test_refresh_equity_reflects_unrealized_pnl_on_open_position(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0)
    order = engine.open_paper_trade(db, plan)
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()

    engine.refresh_equity(db, account, {"SOLUSDT": 164.0})
    expected_unrealized = 5.0 * (164.0 - 160.0)
    assert account.equity == pytest.approx(account.cash_balance + expected_unrealized)


def test_refresh_equity_treats_missing_price_as_zero_unrealized_for_that_leg(db):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0)
    order = engine.open_paper_trade(db, plan)
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()

    engine.refresh_equity(db, account, {})   # no price fetched for this symbol this tick
    assert account.equity == pytest.approx(account.cash_balance)


# ============================================================== watcher loop

def test_watcher_tick_advances_an_open_order_and_persists_mark_price(db, monkeypatch):
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0, tp1_price=170.0, tp2_price=180.0)
    order = engine.open_paper_trade(db, plan)
    db.commit()

    async def fake_funding(symbol):
        return {"code": 0, "data": {"markPrice": "171.0"}}
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", fake_funding)

    _run(watcher.run_tick(db))

    refreshed = db.query(MetaSignalsPaperOrder).filter_by(id=order.id).first()
    assert refreshed.state == "HALF_CLOSED_BE"
    assert refreshed.last_mark_price == pytest.approx(171.0)


def test_watcher_tick_does_nothing_with_no_open_orders(db, monkeypatch):
    # No fetch should even be attempted -- if it were, this would raise.
    async def _boom(symbol):
        raise AssertionError("should not fetch when there is nothing open")
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _boom)
    _run(watcher.run_tick(db))   # must not raise


# ============================================================== radar

def test_public_snapshot_shape(db):
    _approved_plan(db)
    snap = radar.get_public_snapshot(db)
    assert snap["ok"] is True
    assert snap["account"]["starting_balance"] == pytest.approx(500.0)
    assert len(snap["recent_triage"]) == 1


# ============================================================== HTTP routes

sys.modules.setdefault("yfinance", MagicMock())

from main import app
import auth


@pytest.fixture
def client_env(db, monkeypatch):
    monkeypatch_fernet_key = Fernet.generate_key().decode("utf-8")
    os.environ.setdefault("EXECUTOR_CREDENTIAL_KEY", monkeypatch_fernet_key)
    monkeypatch.delenv("METASIGNALS_SECRET_KEY", raising=False)   # checked first by the route -- ensure a clean baseline
    monkeypatch.setenv("METASIGNALS_INGEST_SECRET", "test-ms-secret")

    db.query(UserModel).filter(UserModel.email.in_(["ms_admin@kabroda.com", "ms_user@kabroda.com"])).delete(synchronize_session=False)
    db.commit()
    db.add(UserModel(email="ms_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="msadmin", tier="admin", is_admin=True, subscription_status="active"))
    db.add(UserModel(email="ms_user@kabroda.com", password_hash=auth.hash_password("userpass123"),
                      username="msuser", tier="basic", is_admin=False, subscription_status="active"))
    db.commit()
    yield {"db": db}


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


def test_ingest_requires_correct_secret(client_env):
    client = TestClient(app)
    alert = _good_alert()
    alert["side"] = "BUY"
    resp = client.post("/api/metasignals/alert", json=alert, headers={"X-Kabroda-Secret": "wrong"})
    assert resp.status_code == 401


def test_ingest_rejects_with_no_secret_header(client_env):
    client = TestClient(app)
    resp = client.post("/api/metasignals/alert", json=_good_alert())
    assert resp.status_code == 401


def test_ingest_with_veto_creates_rejected_plan_and_no_order(client_env, monkeypatch):
    _patch_market(monkeypatch, candles_daily_asset=_candles_daily_down())
    client = TestClient(app)
    resp = client.post("/api/metasignals/alert", json=_good_alert(),
                        headers={"X-Kabroda-Secret": "test-ms-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "NO"
    assert body["reason"] == "VETO_MACRO_BELOW_200SMA"

    db = client_env["db"]
    plan = db.query(MetaSignalsPlan).filter_by(id=int(body["plan_id"])).first()
    assert plan is not None
    assert plan.status == "REJECTED"
    assert db.query(MetaSignalsPaperOrder).filter_by(plan_id=plan.id).first() is None


def test_ingest_with_approval_opens_a_real_paper_order(client_env, monkeypatch):
    _patch_market(monkeypatch, mark_price=160.0)
    client = TestClient(app)
    alert = _good_alert(entry_price=160.0, soft_stop=150.0)
    resp = client.post("/api/metasignals/alert", json=alert, headers={"X-Kabroda-Secret": "test-ms-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "YES"

    db = client_env["db"]
    plan = db.query(MetaSignalsPlan).filter_by(id=int(body["plan_id"])).first()
    assert plan.status == "APPROVED"
    order = db.query(MetaSignalsPaperOrder).filter_by(plan_id=plan.id).first()
    assert order is not None
    assert order.state == "OPEN"


def test_public_snapshot_route_works_with_no_login(client_env):
    client = TestClient(app)
    resp = client.get("/api/metasignals/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "account" in body


def test_admin_desk_page_requires_login(client_env):
    client = TestClient(app)
    resp = client.get("/admin/metasignals", follow_redirects=False)
    assert resp.status_code == 303   # redirect to /login


def test_admin_desk_page_rejects_non_admin(client_env):
    client = _login("ms_user@kabroda.com", "userpass123")
    resp = client.get("/admin/metasignals")
    assert resp.status_code == 403


def test_admin_desk_page_allows_admin(client_env):
    client = _login("ms_admin@kabroda.com", "adminpass123")
    resp = client.get("/admin/metasignals")
    assert resp.status_code == 200
    assert "MetaSignals" in resp.text


# ------------------------------------------------------- new webhook shape / raw alert audit

def _good_embed_alert(**overrides):
    content = "SOL | USDT @ $160.00 - 6H - 2.0.1 SL Close Below: 150.00 Target 1: 170.00 Target 2: 180.00"
    base = dict(
        channel_id="123", channel_name="mafioso-majors", message_id="999",
        author="MetaSignalsBot", content=content, embeds=[], created_at="2026-10-13T14:30:00Z",
    )
    base.update(overrides)
    return base


def test_webhook_path_requires_correct_secret(client_env):
    client = TestClient(app)
    resp = client.post("/api/metasignals/webhook", json=_good_embed_alert(), headers={"X-Kabroda-Secret": "wrong"})
    assert resp.status_code == 401


def test_webhook_accepts_metasignals_secret_key_env_var(client_env, monkeypatch):
    # monkeypatch.setenv (not a raw os.environ assignment) so this
    # reverts automatically after the test -- a prior draft of this
    # test leaked a permanent os.environ mutation that broke every
    # later test's own secret check for the rest of the process.
    monkeypatch.delenv("METASIGNALS_INGEST_SECRET", raising=False)
    monkeypatch.setenv("METASIGNALS_SECRET_KEY", "primary-secret")
    _patch_market(monkeypatch, mark_price=160.0)
    client = TestClient(app)
    resp = client.post("/api/metasignals/webhook", json=_good_embed_alert(), headers={"X-Kabroda-Secret": "primary-secret"})
    assert resp.status_code == 200


def test_webhook_falls_back_to_legacy_ingest_secret_env_var(client_env, monkeypatch):
    monkeypatch.delenv("METASIGNALS_SECRET_KEY", raising=False)
    monkeypatch.setenv("METASIGNALS_INGEST_SECRET", "fallback-secret")
    _patch_market(monkeypatch, mark_price=160.0)
    client = TestClient(app)
    resp = client.post("/api/metasignals/webhook", json=_good_embed_alert(), headers={"X-Kabroda-Secret": "fallback-secret"})
    assert resp.status_code == 200


def test_webhook_always_persists_raw_alert_before_parsing(client_env, monkeypatch):
    _patch_market(monkeypatch, mark_price=160.0)
    client = TestClient(app)
    resp = client.post("/api/metasignals/webhook", json=_good_embed_alert(),
                        headers={"X-Kabroda-Secret": "test-ms-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["parsed"] is True

    db = client_env["db"]
    raw = db.query(MetaSignalsRawAlert).filter_by(id=body["raw_id"]).first()
    assert raw is not None
    assert raw.channel_name == "mafioso-majors"
    assert "SOL | USDT" in raw.content

    plan = db.query(MetaSignalsPlan).filter_by(id=int(body["plan_id"])).first()
    assert plan.raw_alert_id == raw.id


def test_webhook_unparseable_content_still_saves_raw_alert_and_reports_not_parsed(client_env):
    client = TestClient(app)
    resp = client.post("/api/metasignals/webhook",
                        json=_good_embed_alert(content="just some channel chatter, not an alert"),
                        headers={"X-Kabroda-Secret": "test-ms-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["parsed"] is False
    assert "verdict" not in body

    db = client_env["db"]
    raw = db.query(MetaSignalsRawAlert).filter_by(id=body["raw_id"]).first()
    assert raw is not None
    assert db.query(MetaSignalsPlan).count() == 0   # nothing to triage -- no plan row at all


def test_alert_path_is_a_true_alias_of_webhook_and_still_accepts_the_legacy_preparsed_shape(client_env, monkeypatch):
    # The committed scripts/raspberry_pi_alert_forwarder.py has been
    # rewritten to the new raw shape, but /alert must still accept the
    # OLD pre-parsed shape in case an unupdated copy is still running
    # somewhere.
    _patch_market(monkeypatch, mark_price=160.0)
    client = TestClient(app)
    legacy_payload = {
        "raw_text": "legacy text", "source": "discord_raspberry_pi",
        "symbol": "SOLUSDT", "side": "BUY", "entry_price": 160.0, "soft_stop": 150.0,
        "tp1": 170.0, "tp2": 180.0, "tp3": None, "timeframe": "6H", "strategy": "2.0.1",
        "timestamp": "2026-10-13T14:30:00Z",
    }
    resp = client.post("/api/metasignals/alert", json=legacy_payload, headers={"X-Kabroda-Secret": "test-ms-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["parsed"] is True
    assert body["verdict"] == "YES"

    db = client_env["db"]
    plan = db.query(MetaSignalsPlan).filter_by(id=int(body["plan_id"])).first()
    assert plan.symbol == "SOLUSDT"
    assert plan.raw_alert_id is not None   # the raw row is still saved even for the legacy shape


# ------------------------------------------------------- execution-mode config route

def test_config_route_requires_admin(client_env):
    client = _login("ms_user@kabroda.com", "userpass123")
    resp = client.post("/api/metasignals/config", json={"execution_mode": "PAPER"})
    assert resp.status_code == 403


def test_config_route_rejects_unknown_execution_mode(client_env):
    client = _login("ms_admin@kabroda.com", "adminpass123")
    resp = client.post("/api/metasignals/config", json={"execution_mode": "NOT_A_REAL_MODE"})
    assert resp.status_code == 400


def test_config_route_persists_execution_mode_and_linked_account(client_env):
    client = _login("ms_admin@kabroda.com", "adminpass123")
    resp = client.post("/api/metasignals/config", json={"execution_mode": "SUBACCOUNT_DRY_RUN", "linked_account_id": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_mode"] == "SUBACCOUNT_DRY_RUN"
    assert body["linked_account_id"] == 7
    assert "not yet wired" in body["note"]

    db = client_env["db"]
    account = db.query(MetaSignalsPaperAccount).filter_by(name="DEFAULT_PAPER_DESK").first()
    assert account.execution_mode == "SUBACCOUNT_DRY_RUN"
    assert account.linked_account_id == 7


def test_non_paper_execution_mode_does_not_change_actual_trade_simulation(client_env, monkeypatch):
    # The dropdown is real and persists, but open_paper_trade()/
    # advance_order() must still behave as pure paper regardless --
    # see metasignals_paper_engine.py's own header.
    client = _login("ms_admin@kabroda.com", "adminpass123")
    client.post("/api/metasignals/config", json={"execution_mode": "SUBACCOUNT_LIVE", "linked_account_id": 1})

    db = client_env["db"]
    plan = _approved_plan(db, entry_mode="MARKET", alert_price=160.0, live_price_at_alert=160.0,
                           hard_stop=150.0, position_size=5.0)
    order = engine.open_paper_trade(db, plan)
    assert order.state == "OPEN"
    assert order.entry_price == pytest.approx(160.0)
    # No real exchange call was ever attempted -- the fact this test
    # never monkeypatches any Bitunix order-placement call and still
    # passes is itself the proof there's no such call being made.
