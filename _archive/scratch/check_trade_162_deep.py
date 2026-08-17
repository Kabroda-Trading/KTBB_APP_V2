"""Deep dive into trade 162 - today's 15m BTC LONG."""
import sqlite3
import json

conn = sqlite3.connect('kabroda.db')
c = conn.cursor()

# Check campaign_logs for trade 162
c.execute("PRAGMA table_info(campaign_logs)")
cols = [r[1] for r in c.fetchall()]
print("campaign_logs columns:", cols)

c.execute("SELECT * FROM campaign_logs WHERE id = 162")
row = c.fetchone()
if row:
    print("\n=== Trade 162 in campaign_logs ===")
    for i, col in enumerate(cols):
        val = row[i]
        if val and len(str(val)) > 200:
            print(f"  {col}: [TRUNCATED - {len(str(val))} chars]")
        else:
            print(f"  {col}: {val}")
else:
    print("\nTrade 162 not in campaign_logs by id")
    # Try finding by trade_id or similar
    c.execute("SELECT * FROM campaign_logs ORDER BY id DESC LIMIT 5")
    rows = c.fetchall()
    print("\nLast 5 campaign_logs:")
    for r in rows:
        print(f"  id={r[0]}, symbol={r[1] if len(cols) > 1 else '?'}")

# Check decision_journal for today
c.execute("SELECT * FROM decision_journal WHERE session_date = '2026-07-16' ORDER BY id DESC")
rows = c.fetchall()
print(f"\n=== Decision Journal entries for 2026-07-16: {len(rows)} ===")
c2 = conn.cursor()
c2.execute("PRAGMA table_info(decision_journal)")
dj_cols = [r[1] for r in c2.fetchall()]
for r in rows:
    print(f"\n  id={r[0]}")
    for i, col in enumerate(dj_cols):
        val = r[i]
        if val and len(str(val)) > 300:
            print(f"    {col}: [TRUNCATED - {len(str(val))} chars]")
        else:
            print(f"    {col}: {val}")

# Check session_locks for today
c.execute("SELECT * FROM session_locks WHERE session_date = '2026-07-16'")
rows = c.fetchall()
print(f"\n=== Session Locks for 2026-07-16: {len(rows)} ===")
c2 = conn.cursor()
c2.execute("PRAGMA table_info(session_locks)")
sl_cols = [r[1] for r in c2.fetchall()]
for r in rows:
    for i, col in enumerate(sl_cols):
        print(f"  {col}: {r[i]}")

conn.close()
