"""Check today's session energy data to understand why trade 162 was called."""
import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect('kabroda.db')
c = conn.cursor()

# Get the most recent session_locks
c.execute("SELECT * FROM session_locks ORDER BY id DESC LIMIT 1")
cols = [d[1] for d in c.execute("PRAGMA table_info(session_locks)")]
r = c.fetchone()
if not r:
    print("No session locks found in local DB")
    conn.close()
    exit()

d = dict(zip(cols, r))
print(f"Session: {d['session_id']} | Date: {d['date_key']} | Symbol: {d['symbol']}")
print(f"Lock time: {datetime.fromtimestamp(d['lock_time'])}")

pkt = json.loads(d['packet_data'])

# === FUEL GAUGE ===
fuel = pkt.get('context', {}).get('fuel_gauge', {})
print("\n=== FUEL GAUGE ===")
for tf in ['15M_JEWEL', '1H', '4H']:
    if tf in fuel:
        f = fuel[tf]
        print(f"\n{tf}:")
        if isinstance(f, dict):
            for k, v in f.items():
                if isinstance(v, dict):
                    print(f"  {k}:")
                    for k2, v2 in v.items():
                        print(f"    {k2}: {v2}")
                else:
                    print(f"  {k}: {v}")

# === BIAS ===
bias = pkt.get('bias_model', {})
print("\n=== BIAS MODEL ===")
print(json.dumps(bias, indent=2))

# === MACRO/MICRO ===
ctx = pkt.get('context', {})
print(f"\nMacro bias: {ctx.get('macro_bias')}")
print(f"Micro bias: {ctx.get('micro_bias')}")
print(f"Micro state: {ctx.get('micro_state')}")
print(f"1H fuel status: {ctx.get('1h_fuel_status')}")

# === LEVELS ===
levels = pkt.get('levels', {})
print("\n=== LEVELS ===")
for k, v in levels.items():
    print(f"  {k}: {v}")

# === MACRO ENVIRONMENT ===
macro_env = ctx.get('macro_environment', {})
print(f"\n=== MACRO ENVIRONMENT ===")
print(json.dumps(macro_env, indent=2))

# === HTF SHELVES ===
shelves = ctx.get('htf_shelves', {})
print(f"\n=== HTF SHELVES ===")
print(json.dumps(shelves, indent=2))

conn.close()
