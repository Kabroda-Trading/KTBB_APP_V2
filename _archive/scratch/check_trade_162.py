"""Check trade 162 details in the database."""
import sqlite3

conn = sqlite3.connect('kabroda.db')
c = conn.cursor()

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

# Find tables that might have trade details
for table in tables:
    if 'trade' in table.lower() or 'ledger' in table.lower() or 'decision' in table.lower() or 'dj' in table.lower():
        c.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in c.fetchall()]
        print(f"\n{table} columns: {cols}")
        
        # Try to find trade 162
        if 'trade_id' in cols or 'id' in cols:
            id_col = 'trade_id' if 'trade_id' in cols else 'id'
            c.execute(f"SELECT * FROM {table} WHERE {id_col} = 162")
            row = c.fetchone()
            if row:
                print(f"  Trade 162 found in {table}:")
                for i, col in enumerate(cols):
                    print(f"    {col}: {row[i]}")
            else:
                print(f"  Trade 162 not found in {table}")

conn.close()
