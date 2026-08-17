# Test the fixed conviction formula (option 1: AND-gate restored + OR path with Revin substitute)

def compute_conviction(tf_data, dominant_direction):
    gate_open = True
    momentum_target = "UP" if dominant_direction == "BULLISH" else "DOWN"
    direction_aligned = sum(1 for v in tf_data.values() if v.get("direction_vote") == dominant_direction)
    momentum_supporting = sum(1 for v in tf_data.values() if v.get("stoch_rsi", {}).get("curl") == momentum_target)
    rwp_squeeze = any(v.get("rwp_squeeze", False) for v in tf_data.values())
    rmo_bullish = any(v.get("rmo_state") == "BULLISH" for v in tf_data.values())
    rmo_bearish = any(v.get("rmo_state") == "BEARISH" for v in tf_data.values())
    rwp_boost = 1 if rwp_squeeze else 0
    rmo_boost = 1 if ((dominant_direction == "BULLISH" and rmo_bullish) or (dominant_direction == "BEARISH" and rmo_bearish)) else 0
    momentum_boost = 1 if momentum_supporting >= 2 else 0
    total_aligned = direction_aligned + rwp_boost + rmo_boost + momentum_boost
    # STRONG if: (old AND-gate) OR (Revin substitutes for momentum)
    conviction = "STRONG" if (direction_aligned >= 3 and momentum_supporting >= 2) or (direction_aligned >= 3 and (rwp_boost + rmo_boost) >= 1) else "MODERATE"
    return conviction, direction_aligned, momentum_supporting, rwp_boost, rmo_boost, momentum_boost, total_aligned

# Test 1: 5 aligned, 0 momentum, 0 RWP, 0 RMO (THE BUG CASE)
tf1 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"} for k in ["15M","1H","4H","1D","1W"]}
c1, da1, ms1, rwp1, rmo1, mb1, ta1 = compute_conviction(tf1, "BULLISH")
print(f"Test 1: 5 aligned, 0 momentum, 0 RWP, 0 RMO")
print(f"  direction={da1} momentum={ms1} rwp={rwp1} rmo={rmo1} total={ta1}")
print(f"  Conviction: {c1} (expected: MODERATE — no momentum, no Revin)")
assert c1 == "MODERATE", f"FAIL: expected MODERATE, got {c1}"
print("  PASS")
print()

# Test 2: 3 aligned, 2 momentum, 0 RWP, 0 RMO
tf2 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "UP"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"} for k in ["15M","1H","4H"]}
tf2["1D"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
tf2["1W"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
c2, da2, ms2, rwp2, rmo2, mb2, ta2 = compute_conviction(tf2, "BULLISH")
print(f"Test 2: 3 aligned, 2 momentum, 0 RWP, 0 RMO")
print(f"  direction={da2} momentum={ms2} rwp={rwp2} rmo={rmo2} total={ta2}")
print(f"  Conviction: {c2} (expected: STRONG — AND-gate passes)")
assert c2 == "STRONG", f"FAIL: expected STRONG, got {c2}"
print("  PASS")
print()

# Test 3: 3 aligned, 0 momentum, 0 RWP, 0 RMO
tf3 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"} for k in ["15M","1H","4H"]}
tf3["1D"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
tf3["1W"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
c3, da3, ms3, rwp3, rmo3, mb3, ta3 = compute_conviction(tf3, "BULLISH")
print(f"Test 3: 3 aligned, 0 momentum, 0 RWP, 0 RMO")
print(f"  direction={da3} momentum={ms3} rwp={rwp3} rmo={rmo3} total={ta3}")
print(f"  Conviction: {c3} (expected: MODERATE — nothing)")
assert c3 == "MODERATE", f"FAIL: expected MODERATE, got {c3}"
print("  PASS")
print()

# Test 4: 3 aligned, 0 momentum, RWP squeeze + RMO bullish (Revin substitutes)
tf4 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": True, "rmo_state": "BULLISH"} for k in ["15M","1H","4H"]}
tf4["1D"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
tf4["1W"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
c4, da4, ms4, rwp4, rmo4, mb4, ta4 = compute_conviction(tf4, "BULLISH")
print(f"Test 4: 3 aligned, 0 momentum, RWP+RMO (Revin substitutes)")
print(f"  direction={da4} momentum={ms4} rwp={rwp4} rmo={rmo4} total={ta4}")
print(f"  Conviction: {c4} (expected: STRONG — Revin substitutes for momentum)")
assert c4 == "STRONG", f"FAIL: expected STRONG, got {c4}"
print("  PASS")
print()

# Test 5: 5 aligned, 0 momentum, RWP squeeze only (Revin substitutes)
tf5 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": True, "rmo_state": "NEUTRAL"} for k in ["15M","1H","4H","1D","1W"]}
c5, da5, ms5, rwp5, rmo5, mb5, ta5 = compute_conviction(tf5, "BULLISH")
print(f"Test 5: 5 aligned, 0 momentum, RWP squeeze only")
print(f"  direction={da5} momentum={ms5} rwp={rwp5} rmo={rmo5} total={ta5}")
print(f"  Conviction: {c5} (expected: STRONG — Revin substitutes)")
assert c5 == "STRONG", f"FAIL: expected STRONG, got {c5}"
print("  PASS")
print()

# Test 6: 3 aligned, 0 momentum, RMO only (Revin substitutes)
tf6 = {k: {"direction_vote": "BULLISH", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "BULLISH"} for k in ["15M","1H","4H"]}
tf6["1D"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
tf6["1W"] = {"direction_vote": "NEUTRAL", "stoch_rsi": {"curl": "DOWN"}, "rwp_squeeze": False, "rmo_state": "NEUTRAL"}
c6, da6, ms6, rwp6, rmo6, mb6, ta6 = compute_conviction(tf6, "BULLISH")
print(f"Test 6: 3 aligned, 0 momentum, RMO only")
print(f"  direction={da6} momentum={ms6} rwp={rwp6} rmo={rmo6} total={ta6}")
print(f"  Conviction: {c6} (expected: STRONG — Revin substitutes)")
assert c6 == "STRONG", f"FAIL: expected STRONG, got {c6}"
print("  PASS")
print()

print("ALL TESTS PASSED")
