"""One-time cleanup: remove stale duplicate GravityMemory levels."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, GravityMemory
from datetime import datetime, timezone
from collections import defaultdict

db = SessionLocal()
try:
    # 1. Count everything
    total = db.query(GravityMemory).count()
    active = db.query(GravityMemory).filter(GravityMemory.active == True).count()
    inactive = db.query(GravityMemory).filter(GravityMemory.active == False).count()
    print(f"Total rows: {total}")
    print(f"  Active:   {active}")
    print(f"  Inactive: {inactive}")
    print()

    # 2. Find duplicates: same source + level_type + symbol, keep only most recent
    all_rows = db.query(GravityMemory).order_by(GravityMemory.timestamp.desc()).all()
    
    groups = defaultdict(list)
    for row in all_rows:
        key = (row.source, row.level_type, row.symbol)
        groups[key].append(row)
    
    total_deactivated = 0
    total_kept = 0
    
    for key, rows in groups.items():
        # First row is most recent (sorted desc)
        keep = rows[0]
        deactivate = rows[1:]
        
        if deactivate:
            ids = [r.id for r in deactivate]
            db.query(GravityMemory).filter(GravityMemory.id.in_(ids)).update(
                {"active": False}, synchronize_session=False
            )
            total_deactivated += len(deactivate)
            total_kept += 1
            print(f"  {key[0]:20s} | {key[1]:20s} | {key[2]:10s} | kept id={keep.id} (${keep.price:>8,.2f}), deactivated {len(deactivate)} older rows")
        else:
            total_kept += 1
    
    db.commit()
    print()
    print(f"=== CLEANUP COMPLETE ===")
    print(f"Kept:        {total_kept}")
    print(f"Deactivated: {total_deactivated}")
    
    # 3. Reset touch_count and departure_move_pct for all remaining active rows
    reset_count = db.query(GravityMemory).filter(
        GravityMemory.active == True
    ).update({
        "touch_count": 0,
        "departure_move_pct": None
    }, synchronize_session=False)
    db.commit()
    print(f"Reset touch/departure data: {reset_count} rows")
    print()

    # 4. Show what's left
    remaining = db.query(GravityMemory).filter(GravityMemory.active == True).order_by(GravityMemory.price).all()
    print(f"=== REMAINING ACTIVE LEVELS ({len(remaining)}) ===")
    for r in remaining:
        print(f"  id={r.id:4d} | ${r.price:>10,.2f} | {r.level_type:20s} | {r.source:20s} | class={r.permanence_class} | heat={r.heat_multiplier}")

finally:
    db.close()
