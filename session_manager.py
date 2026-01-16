# session_manager.py — Session Timing Reference Engine

from typing import List, Dict

def get_session_schedule() -> List[Dict[str, str]]:
    """
    Returns standardized session schedule for futures and equities markets.
    Times are in UTC. Used to determine when market energy is active.
    """
    return [
        {
            "name": "Asia",
            "market": "Equities",
            "open": "00:00",
            "close": "09:00"
        },
        {
            "name": "Europe",
            "market": "Equities",
            "open": "07:00",
            "close": "16:00"
        },
        {
            "name": "New York Futures",
            "market": "Futures",
            "open": "11:00",  # 6:00 AM EST
            "close": "22:00"  # 5:00 PM EST
        },
        {
            "name": "New York Equities",
            "market": "Equities",
            "open": "14:30",  # 9:30 AM EST
            "close": "21:00"  # 4:00 PM EST
        }
    ]
