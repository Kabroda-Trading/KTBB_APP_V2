# CC: Workspace Root Fix

**Problem:** The workspace root is currently mapped to `bold-hubble/`, but the main Kabroda trading application lives in the parent `KTBB_app_v2/` directory. This means the AI agent can't see `main.py`, `session_manager.py`, templates, Phase 2 engines, or any of the core application files — it can only see the KQAL monitoring layer.

**Fix:** Change the workspace root from `bold-hubble/` to `KTBB_app_v2/`. No files need to move. `bold-hubble/` stays as a subdirectory within the project.

**Steps:**
1. Update the workspace configuration so the root is `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2` instead of `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\bold-hubble`
2. Verify the AI agent can now see `main.py`, `templates/`, `config/`, `session_manager.py`, `session_monitor.py`, and all `signal_*.py` files at the root level
3. Confirm `bold-hubble/` is still accessible as a subdirectory

**No files are moved, no paths are changed, no git history is affected.** This is purely a workspace configuration change.
