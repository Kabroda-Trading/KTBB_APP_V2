"""Check today's trade status from the local DB."""
import sqlite3
import json
from datetime import datetime, timezone

conn = sqlite3.connect('kabroda.db')
c = conn.cursor()

# 1. Check decision_journal for today
c.execute("SELECT * FROM decision_journal WHERE session_date = '2026-07-16' ORDER BY id DESC")
cols = [d[1] for d in c.execute("PRAGMA table_info(decision_journal)")]
rows = c.fetchall()
print(f"=== Decision Journal for 2026-07-16: {len(rows)} entries ===")
for r in rows:
    d = dict(zip(cols, r))
    print(f"  id={d['id']} | type={d['decision_type']} | symbol={d['symbol']} | bo={d['bo_price']} | bd={d['bd_price']}")
    ctx = json.loads(d['full_context_json']) if d['full_context_json'] and d['full_context_json'] != '{}' else {}
    if ctx:
        print(f"    context keys: {list(ctx.keys())[:10]}")

# 2. Check campaign_logs for today
c.execute("SELECT * FROM campaign_logs WHERE date_key = '2026-07-16' ORDER BY id DESC")
cols2 = [d[1] for d in c.execute("PRAGMA table_info(campaign_logs)")]
rows2 = c.fetchall()
print(f"\n=== Campaign Logs for 2026-07-16: {len(rows2)} entries ===")
for r in rows2:
    d = dict(zip(cols2, r))
    print(f"  id={d['id']} | symbol={d['symbol']} | bias={d['bias']} | status={d['status']} | entry={d['entry_price']} | stop={d['stop_loss']} | pnl={d['realized_pnl']} | target_hit={d['target_hit']}")
    if d['closed_at']:
        print(f"    closed_at={d['closed_at']}")

# 3. Check session_locks for today
c.execute("SELECT * FROM session_locks WHERE date_key = '2026-07-16' ORDER BY id DESC")
cols3 = [d[1] for d in c.execute("PRAGMA table_info(session_locks)")]
rows3 = c.fetchall()
print(f"\n=== Session Locks for 2026-07-16: {len(rows3)} entries ===")
for r in rows3:
    d = dict(zip(cols3, r))
    print(f"  id={d['id']} | symbol={d['symbol']} | session={d['session_id']} | date={d['date_key']}")

# 4. Check the most recent campaign_logs overall
c.execute("SELECT * FROM campaign_logs ORDER BY id DESC LIMIT 10")
rows4 = c.fetchall()
print(f"\n=== Most Recent Campaign Logs (last 10) ===")
for r in rows4:
    d = dict(zip(cols2, r))
    print(f"  id={d['id']} | date={d['date_key']} | symbol={d['symbol']} | bias={d['bias']} | status={d['status']} | pnl={d['realized_pnl']}")

conn.close()
