# metasignals_triage.py
# ==============================================================================
# METASIGNALS 5-PILLAR TRIAGE GATE -- Andy directive 2026-10-13 09:34 CT,
# METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md section 3. Evaluates an incoming
# Discord alert (forwarded by scripts/raspberry_pi_alert_forwarder.py)
# against five quantitative pillars using live Bitunix market data. All
# five must pass for an APPROVED ("YES") verdict; any single failure is a
# named VETO_* ("NO"), zero capital at risk either way (this is the gate
# in front of the PAPER desk, not a real order).
#
# IRON WALL (spec section 2A): this module must never import, call, or
# touch gate_traveler.py, traveler_plan_engine.py / traveler_plan_notify.py
# / traveler_radar.py, battlebox_pipeline.py, executor_live_e1_engine.py /
# executor_plan_builder.py / executor_sizing.py, or any alt_matrix_*.py
# module. Every pure-math helper below (EMA/SMA/ATR/RSI) is therefore
# reimplemented FRESH here from the standard public formulas, exactly the
# same "copy the pattern, never import the file" discipline
# alt_matrix_signals.py already established for its own EMA/ATR instead of
# importing gate_traveler.py. market_data.py and executor_bitunix_client.py
# are explicitly NOT on the forbidden list (both are already-generic, zero-
# coupling utilities Alt Matrix itself reuses directly) -- safe to import.
#
# SCOPE DECISIONS made resolving real ambiguity in the spec (flagged here,
# not silently picked, per this project's standing discipline):
#   1. LONG-ONLY. The spec's every formula, example, and worked number
#      (macro "not into bear flushes," RSI floor/ceiling framed around a
#      squeeze, stop below entry, TP above entry) describes a long trade,
#      and the Pi forwarder hardcodes side="BUY". A SHORT alert is vetoed
#      outright (VETO_SIDE_NOT_SUPPORTED) rather than silently run through
#      long-only math in the wrong direction -- a short would need its own
#      mirrored formulas this spec never specified.
#   2. Pillar 1's asset leg ("Daily Close > Daily 200 SMA (or Daily 55
#      EMA)") is read as: use the 200 SMA when the asset has enough daily
#      history to compute one; fall back to the 55 EMA only when it
#      doesn't (a brand-new listing). BTC's own leg always uses the 200
#      SMA -- BTC obviously has the history, no fallback needed there.
#   3. TP2 = the alert's own stated tp2, verbatim. The spec's "(or trail
#      8H 21 EMA)" alternative is a genuinely separate, unspecified
#      mechanism (bar cadence, update rule, nothing given) -- building it
#      now would be inventing behavior nobody actually specified. If
#      Andy wants the EMA-trail variant, that is a fresh, separately-
#      scoped addition, not assumed here.
#   4. Pillar 5's strategy whitelist has only two named examples in the
#      spec ("2.0.1", "2.0 G2") -- WHITELISTED_STRATEGIES below contains
#      exactly those two, nothing invented beyond them. Adding a new
#      strategy name is a one-line edit to that set.
#      2026-10-13 (Andy, confirmed via AskUserQuestion after a real live
#      alert was checked against the actual triage log, not taken on a
#      relayed claim): added "1.0 G2" and timeframe "3H" -- the first
#      real production alert came in as symbol=SOLUSDT (already
#      whitelisted, a wrong detail in the report that prompted this),
#      strategy="1.0 G2", timeframe="3H", and was correctly vetoed
#      VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED on BOTH the strategy and
#      timeframe checks at once (the relayed report only mentioned the
#      timeframe) -- confirmed Andy wants both admitted, not just one.
#   5. "Live Bitunix price" for the extension check and the market-entry
#      fill reference is markPrice from the SAME fetch_public_funding_rate
#      call already used for pillar 4 -- one network call does double
#      duty (funding rate + mark price), reusing the one endpoint this
#      codebase has already live-verified as public/unauthenticated
#      (executor_bitunix_client.py's own header), rather than building a
#      second, never-checked ticker endpoint for a price the first call
#      already carries.
#   6. The spec's own "evaluates in under 100ms" framing is aspirational
#      and not literally achievable here -- this function makes real
#      network calls (two candle fetches, one funding-rate fetch) that
#      will typically take well over 100ms end to end. Flagged plainly
#      rather than silently claimed; nothing about correctness depends on
#      that number.
# ==============================================================================

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import market_data

WHITELISTED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
WHITELISTED_TIMEFRAMES = ("6H", "8H", "3H")
WHITELISTED_STRATEGIES = ("2.0.1", "2.0 G2", "1.0 G2")   # see module header, decision 4, incl. the 2026-10-13 addition

# Discord content parsing (2026-10-13, spec section 6A/commit `f770ec7`):
# the Raspberry Pi daemon now forwards the RAW Discord payload (content +
# embeds) and parsing moved server-side -- the Pi script itself
# (scripts/raspberry_pi_alert_forwarder.py) was rewritten the same day to
# drop its own copy of this logic entirely, so there is now exactly ONE
# copy of these patterns, not two that could drift.
#
# The numeric capture groups below are tightened from the original Pi
# script's own `[\d\.]+` (any run of digits/periods) to a proper decimal
# pattern `\d+(?:\.\d+)?` -- caught by this module's own test suite: the
# original pattern greedily swallows a trailing sentence-ending period
# directly after a value (e.g. "...Below: 105.40." parsed as "105.40.",
# which float() rejects). Harmless after the Pi script's own field-value
# formatting (clean numbers, no trailing punctuation) but a real
# fragility once this is the ONLY parser and the sole input is whatever
# raw text Discord happens to contain.
#
# The strategy group had a SECOND, more serious bug of the same kind,
# also caught by this module's own test suite against realistic
# space-joined field text (the Pi's own embed-flattening joins "name:
# value" pairs with spaces, not newlines): the original `[0-9A-Za-z\.\s]+`
# is unbounded and happily keeps consuming past the end of the real
# strategy name straight into the NEXT field's own label text (e.g.
# "2.0 G2 SL Close Below" instead of "2.0 G2") -- since "SL"/"Close"/
# "Below" are themselves just letters, nothing in the original pattern
# ever stopped there. A corrupted strategy string can never match
# WHITELISTED_STRATEGIES, which would silently veto every real alert.
# Fixed with a lazy capture bounded by a lookahead for the next known
# field label (SL Close Below: / Target N:) or end of string, rather
# than guessing a fixed strategy-name shape.
_NEXT_FIELD_LOOKAHEAD = r"(?=\s*(?:SL\s+Close\s+Below\s*:|Target\s*\d\s*:|$))"
_ALERT_PATTERN = re.compile(
    r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>\d+(?:\.\d+)?)\s*-\s*(?P<timeframe>[0-9A-Za-z]+)\s*-\s*"
    r"(?P<strategy>[0-9A-Za-z][0-9A-Za-z\.\s]*?)" + _NEXT_FIELD_LOOKAHEAD,
    re.IGNORECASE,
)
_SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>\d+(?:\.\d+)?)", re.IGNORECASE)
_T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>\d+(?:\.\d+)?)", re.IGNORECASE)
_T2_PATTERN = re.compile(r"Target 2:\s*(?P<tp2>\d+(?:\.\d+)?)", re.IGNORECASE)
_T3_PATTERN = re.compile(r"Target 3:\s*(?P<tp3>\d+(?:\.\d+)?)", re.IGNORECASE)

RSI_FLOOR = 40.0
RSI_CEILING = 62.0
EXTENSION_MARKET_MAX_R = 0.10
EXTENSION_LIMIT_MAX_R = 0.25
FUNDING_VETO_THRESHOLD = 0.0004   # +0.04% per spec section 3, pillar 4
HARD_STOP_ATR_MULTIPLE = 0.25
TP1_R_MULTIPLE = 1.0


# ------------------------------------------------------------------ pure math (no I/O)

def _closes(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["close"]) for c in candles]


def sma_series(values: List[float], window: int) -> List[Optional[float]]:
    """Plain rolling mean, None before `window` values exist -- same
    formula/semantics as alt_matrix_signals.sma_series(), reimplemented
    here rather than imported (Iron Wall, see module header)."""
    out: List[Optional[float]] = []
    running_sum = 0.0
    for i, v in enumerate(values):
        running_sum += v
        if i >= window:
            running_sum -= values[i - window]
        out.append(running_sum / window if i >= window - 1 else None)
    return out


def ema_series(values: List[float], span: int) -> List[Optional[float]]:
    """EMA[0] = values[0], EMA[t] = alpha*v + (1-alpha)*EMA[t-1], alpha =
    2/(span+1) -- pandas' ewm(span=span, adjust=False).mean(), same
    formula as alt_matrix_signals.ema_series(), reimplemented fresh."""
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def atr14_wilder_series(candles: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Standard Wilder true-range ATR(14) -- same formula as
    alt_matrix_signals.atr14_wilder_series(), reimplemented fresh (Iron
    Wall, see module header)."""
    if not candles:
        return []
    trs: List[float] = []
    prev_close = None
    for c in candles:
        high, low, close = float(c["high"]), float(c["low"]), float(c["close"])
        if prev_close is None:
            trs.append(high - low)
        else:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    out: List[Optional[float]] = [None] * len(trs)
    if len(trs) < 14:
        return out
    seed = sum(trs[:14]) / 14.0
    out[13] = seed
    atr = seed
    for i in range(14, len(trs)):
        atr = (atr * 13 + trs[i]) / 14.0
        out[i] = atr
    return out


def rsi14_wilder_series(closes: List[float]) -> List[Optional[float]]:
    """Standard Wilder RSI(14): average gain/loss seeded as a plain mean
    of the first 14 deltas, then Wilder-smoothed (avg = (avg*13+new)/14)
    thereafter. Returns one value per input close, None for the first 14
    indices (no RSI is defined until 14 deltas exist). avg_loss == 0 is
    treated as RSI == 100 (textbook convention: no losses at all in the
    window is maximal strength, not a divide-by-zero)."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < 15:
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:14]) / 14.0
    avg_loss = sum(losses[:14]) / 14.0
    out[14] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(14, len(deltas)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14.0
        avg_loss = (avg_loss * 13 + losses[i]) / 14.0
        out[i + 1] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


# ------------------------------------------------------------------ market data (I/O, generic reused fetchers)

async def _fetch_confirmed_4h(symbol: str, target_bars: int = 120) -> List[Dict[str, Any]]:
    candles = await market_data.fetch_bitunix_4h(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 14400, now_ts=time.time())


async def _fetch_confirmed_daily(symbol: str, target_bars: int = 250) -> List[Dict[str, Any]]:
    candles = await market_data.fetch_bitunix_daily(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 86400, now_ts=time.time())


async def _fetch_funding_and_mark(symbol: str) -> Dict[str, Optional[float]]:
    """One call does double duty: funding rate (pillar 4) + markPrice
    (pillar 3's extension check and the market-entry fill reference) --
    see module header, decision 5. Returns {"funding_rate": None,
    "mark_price": None} on ANY failure -- fails closed, never guesses."""
    import executor_bitunix_client
    try:
        resp = await executor_bitunix_client.fetch_public_funding_rate(symbol.replace("/", ""))
    except Exception:
        return {"funding_rate": None, "mark_price": None}
    if resp.get("code") not in (0, None):
        return {"funding_rate": None, "mark_price": None}
    data = resp.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return {"funding_rate": None, "mark_price": None}
    out: Dict[str, Optional[float]] = {"funding_rate": None, "mark_price": None}
    try:
        if data.get("fundingRate") is not None:
            out["funding_rate"] = float(data["fundingRate"])
    except (TypeError, ValueError):
        pass
    try:
        if data.get("markPrice") is not None:
            out["mark_price"] = float(data["markPrice"])
    except (TypeError, ValueError):
        pass
    return out


def parse_discord_alert(content: Optional[str], embeds: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    """Pure text parser -- no I/O, no DB, matching this module's own
    Iron Wall discipline. Takes the raw webhook payload's `content` +
    `embeds` (embeds, if present, are dicts with optional title/
    description/fields -- the same shape the Pi daemon's own on_message
    handler already builds `text` from), returns a dict with symbol/
    side/entry_price/soft_stop/tp1/tp2/tp3/timeframe/strategy ready to
    hand to evaluate_alert(), or None if this message doesn't look like
    a recognized MetaSignals trade alert at all (ordinary channel
    chatter, a message missing the SL line, etc.) -- a None here is
    NOT an error, just "nothing to triage," exactly how the Pi's own
    on_message() silently returns on a non-match today."""
    text = content or ""
    for embed in (embeds or []):
        if not isinstance(embed, dict):
            continue
        if embed.get("title"):
            text += " " + str(embed["title"])
        if embed.get("description"):
            text += " " + str(embed["description"])
        for field in (embed.get("fields") or []):
            if isinstance(field, dict):
                text += f" {field.get('name', '')}: {field.get('value', '')}"

    match = _ALERT_PATTERN.search(text)
    if not match:
        return None

    symbol = f"{match.group('symbol').upper()}USDT"
    entry_price = float(match.group("entry"))
    timeframe = match.group("timeframe").strip().upper()
    strategy = match.group("strategy").strip()

    sl_match = _SL_PATTERN.search(text)
    if not sl_match:
        return None   # matched the header but no SL line -- same "skip, don't guess" rule as the Pi's own parser
    soft_stop = float(sl_match.group("sl"))

    tp1_match = _T1_PATTERN.search(text)
    tp2_match = _T2_PATTERN.search(text)
    tp3_match = _T3_PATTERN.search(text)
    tp1 = float(tp1_match.group("tp1")) if tp1_match else round(entry_price * 1.04, 4)
    tp2 = float(tp2_match.group("tp2")) if tp2_match else round(entry_price * 1.08, 4)
    tp3 = float(tp3_match.group("tp3")) if tp3_match else None

    return {
        "symbol": symbol, "side": "BUY", "entry_price": entry_price, "soft_stop": soft_stop,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "timeframe": timeframe, "strategy": strategy,
        "raw_text": text,
    }


# ------------------------------------------------------------------ the gate itself

async def evaluate_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """The full 5-pillar gate for one ingested alert. `alert` is the raw
    payload dict (already validated by main.py's pydantic model) with at
    least: symbol, side, entry_price, soft_stop, tp1, tp2, timeframe,
    strategy. Returns a dict with "verdict" ("YES"|"NO"), "reason" (a
    named VETO_* on NO, "APPROVED" on YES), every pillar readout (for
    audit, regardless of verdict), and -- only when verdict=="YES" --
    the computed geometry (hard_stop, risk_usd, position_size, tp1_price,
    tp2_price, entry_mode).

    Checks run in the pillar order given in the spec; the FIRST failing
    pillar's reason is returned (no partial credit, matching every other
    gate in this codebase's own "all must pass" convention)."""
    symbol = str(alert.get("symbol", "")).upper()
    side = str(alert.get("side", "BUY")).upper()
    timeframe = str(alert.get("timeframe", "")).upper()
    strategy = str(alert.get("strategy", "")).strip()
    alert_price = float(alert["entry_price"])
    alert_soft_stop = float(alert["soft_stop"])

    out: Dict[str, Any] = {"symbol": symbol}

    # Pillar 5 first -- cheapest check, no network I/O, and the spec's
    # own ordering lists it last but every other pillar's math is moot
    # on an un-whitelisted pair/timeframe/strategy.
    if symbol not in WHITELISTED_SYMBOLS or timeframe not in WHITELISTED_TIMEFRAMES or strategy not in WHITELISTED_STRATEGIES:
        out.update(verdict="NO", reason="VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED")
        return out

    if side != "BUY":
        out.update(verdict="NO", reason="VETO_SIDE_NOT_SUPPORTED")
        return out

    candles_4h = await _fetch_confirmed_4h(symbol)
    candles_1d = await _fetch_confirmed_daily(symbol)
    candles_btc_1d = await _fetch_confirmed_daily("BTCUSDT")
    funding_mark = await _fetch_funding_and_mark(symbol)

    if len(candles_4h) < 15:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out
    if len(candles_1d) < 56 or len(candles_btc_1d) < 201:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_DAILY_HISTORY")
        return out

    # --- Pillar 1: Macro Trend ---
    btc_closes = _closes(candles_btc_1d)
    btc_sma200 = sma_series(btc_closes, 200)[-1]
    btc_daily_close = btc_closes[-1]

    asset_closes = _closes(candles_1d)
    asset_daily_close = asset_closes[-1]
    asset_sma200_series = sma_series(asset_closes, 200)
    asset_sma200 = asset_sma200_series[-1]
    asset_ema55 = ema_series(asset_closes, 55)[-1]

    out.update(
        btc_daily_close=btc_daily_close, btc_sma200=btc_sma200,
        asset_daily_close=asset_daily_close, asset_sma200=asset_sma200, asset_ema55=asset_ema55,
    )

    btc_macro_pass = btc_sma200 is not None and btc_daily_close > btc_sma200
    if asset_sma200 is not None:
        asset_macro_pass = asset_daily_close > asset_sma200
    else:
        asset_macro_pass = asset_ema55 is not None and asset_daily_close > asset_ema55   # decision 2, module header

    if not (btc_macro_pass and asset_macro_pass):
        out.update(verdict="NO", reason="VETO_MACRO_BELOW_200SMA")
        return out

    # --- Pillar 2: Energy / Exhaustion ---
    closes_4h = _closes(candles_4h)
    rsi_4h = rsi14_wilder_series(closes_4h)[-1]
    out["rsi_4h"] = rsi_4h

    if rsi_4h is None:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out
    if rsi_4h > RSI_CEILING:
        out.update(verdict="NO", reason="VETO_MOMENTUM_OVERBOUGHT")
        return out
    if rsi_4h < RSI_FLOOR:
        out.update(verdict="NO", reason="VETO_MOMENTUM_COLLAPSED")
        return out

    # --- ATR14 (4H) -- feeds both pillar 3's extension calc and the hard-stop buffer ---
    atr14_4h = atr14_wilder_series(candles_4h)[-1]
    out["atr14_4h"] = atr14_4h
    if atr14_4h is None or atr14_4h <= 0:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out

    hard_stop = alert_soft_stop - HARD_STOP_ATR_MULTIPLE * atr14_4h
    r_distance = alert_price - hard_stop
    out["hard_stop"] = hard_stop
    if r_distance <= 0:
        # The alert's own soft_stop sits at or above the alert price --
        # a malformed/contradictory alert, not a real setup. Fails
        # closed rather than computing a nonsensical negative-risk size.
        out.update(verdict="NO", reason="VETO_INVALID_STOP_GEOMETRY")
        return out

    # --- Pillar 3: Extension ("Don't Chase") ---
    mark_price = funding_mark["mark_price"]
    out["live_price_at_alert"] = mark_price
    if mark_price is None:
        out.update(verdict="NO", reason="VETO_PRICE_FEED_UNAVAILABLE")
        return out

    extension_r = (mark_price - alert_price) / r_distance
    out["extension_r"] = extension_r

    if extension_r <= EXTENSION_MARKET_MAX_R:
        entry_mode = "MARKET"
    elif extension_r <= EXTENSION_LIMIT_MAX_R:
        entry_mode = "LIMIT"
    else:
        out.update(verdict="NO", reason="VETO_PRICE_OVER_EXTENDED")
        return out

    # --- Pillar 4: Funding Crowding ---
    funding_rate = funding_mark["funding_rate"]
    out["funding_rate"] = funding_rate
    if funding_rate is None:
        out.update(verdict="NO", reason="VETO_FUNDING_UNAVAILABLE")
        return out
    if funding_rate >= FUNDING_VETO_THRESHOLD:
        out.update(verdict="NO", reason="VETO_FUNDING_OVERCROWDED")
        return out

    # --- All five pillars passed -- compute final geometry ---
    position_size = 50.0 / r_distance
    tp1_price = alert_price + TP1_R_MULTIPLE * r_distance
    tp2_price = float(alert["tp2"])

    out.update(
        verdict="YES", reason="APPROVED",
        entry_mode=entry_mode,
        risk_usd=50.0,
        position_size=position_size,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
    )
    return out
