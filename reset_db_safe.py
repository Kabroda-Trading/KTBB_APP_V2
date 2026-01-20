# reset_db_safe.py
# ==============================================================================
# DATABASE RESET & ADMIN RESTORATION TOOL
# ==============================================================================
# Usage: python reset_db_safe.py
# Risk: HIGH. Deletes all non-admin users.
# ==============================================================================

import sys
from sqlalchemy import text
from database import engine, SessionLocal, UserModel, SystemLog

# --- CONFIGURATION ---
# PUT YOUR TWO ADMIN EMAILS HERE
ADMIN_EMAILS = [
    "spiritmaker79@gmail.com", 
    "grossmonkeytrader@protonmail.com"
]

def reset_and_restore():
    print(f"!!! WARNING: This will WIPE the database except for: {ADMIN_EMAILS}")
    confirm = input("Type 'CONFIRM' to proceed: ")
    if confirm != "CONFIRM":
        print("Operation aborted.")
        return

    db = SessionLocal()
    try:
        # 1. DELETE SYSTEM LOGS (Clean Slate)
        print(">>> Wiping System Logs...")
        db.query(SystemLog).delete()
        
        # 2. IDENTIFY ADMINS VS PLEBS
        all_users = db.query(UserModel).all()
        kept_count = 0
        deleted_count = 0

        for user in all_users:
            if user.email in ADMIN_EMAILS:
                print(f">>> PRESERVING ADMIN: {user.email}")
                user.is_admin = True  # Enforce God Mode
                user.operator_flex = True # Give them Flex access too
                kept_count += 1
            else:
                print(f"--- DELETING USER: {user.email}")
                db.delete(user)
                deleted_count += 1
        
        db.commit()
        print("="*40)
        print(f"DONE. Kept {kept_count} Admins. Deleted {deleted_count} Users.")
        print("="*40)

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    reset_and_restore()