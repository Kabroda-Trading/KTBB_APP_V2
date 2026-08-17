import re
from typing import Dict, Any, Optional

class MafiosoSignalParser:
    """
    Parses and evaluates automated alerts from any Meta Signals (#mafioso-long/short/majors) Discord channel.
    """
    
    SIGNAL_PATTERN = re.compile(
        r"(?P<direction>Long|Short)\s+MTF\s+Alert\s+"
        r"(?P<asset>[A-Z0-9]+)\s*\|\s*USDT\s+@\s+\$?(?P<entry>[\d,.]+)\s+-\s+(?P<tf>\d+[HM])\s+-\s+(?P<grade>[A-Z0-9.\s]+)\s+"
        r"Target 1:\s*(?P<tp1>[\d,.]+)\s*\(RR\s*(?P<rr1>[\d.]+)\)\s+"
        r"Target 2:\s*(?P<tp2>[\d,.]+)\s*\(RR\s*(?P<rr2>[\d.]+)\)\s+"
        r"Target 3:\s*(?P<tp3>[\d,.]+)\s*\(RR\s*(?P<rr3>[\d.]+)\)\s+"
        r"SL Close (?P<sl_dir>Above|Below):\s*(?P<sl>[\d,.]+)",
        re.IGNORECASE
    )

    @staticmethod
    def parse_alert_text(text: str) -> Optional[Dict[str, Any]]:
        """
        Parses raw text from Meta Signals into a structured Python dictionary ready for execution.
        """
        match = MafiosoSignalParser.SIGNAL_PATTERN.search(text)
        if not match:
            return None
            
        def clean_num(val: str) -> float:
            return float(val.replace(",", ""))
            
        data = match.groupdict()
        direction = data['direction'].upper()
        return {
            "strategy": f"Meta_Signals_{direction}_MTF",
            "pair": f"{data['asset']}/USDT",
            "timeframe": data['tf'],
            "algo_grade": data['grade'].strip(),
            "direction": direction,
            "entry_price": clean_num(data['entry']),
            "targets": [
                {"level": 1, "price": clean_num(data['tp1']), "risk_reward": float(data['rr1'])},
                {"level": 2, "price": clean_num(data['tp2']), "risk_reward": float(data['rr2'])},
                {"level": 3, "price": clean_num(data['tp3']), "risk_reward": float(data['rr3'])},
            ],
            "stop_loss": {
                "price": clean_num(data['sl']),
                "execution_rule": f"CANDLE_CLOSE_{data['sl_dir'].upper()}",
                "note": f"Must wait for confirmed {data['tf']} candle close {data['sl_dir'].lower()} {data['sl']} before exiting (ignore intra-bar wicks)."
            }
        }

if __name__ == "__main__":
    sample_alert = """
    Short MTF Alert
    BTC | USDT @ $64,173.60 - 4H - 3.0 G1
    Target 1: 62,491.02 (RR 1.03)
    Target 2: 61,164.44 (RR 1.84)
    Target 3: 56,875.99 (RR 4.46)
    SL Close Above: 65,810.03
    """
    parsed = MafiosoSignalParser.parse_alert_text(sample_alert)
    import json
    print(json.dumps(parsed, indent=2))
