# session_manager.py — Provides standardized session schedule
from typing import List, Dict

def get_session_schedule() -> List[Dict[str, str]]:
    """
    Returns the daily trading session schedule.
    Each session includes a name and open/close time in UTC.
    """
    return [
        {"name": "Asia", "open": "00:00", "close": "09:00"},
        {"name": "Europe", "open": "07:00", "close": "16:00"},
        {"name": "New York", "open": "13:00", "close": "22:00"}
    ]
