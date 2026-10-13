#!/usr/bin/env python3
"""
raspberry_pi_alert_forwarder.py
=============================================================================
Raspberry Pi Air-Gapped Discord Listener for MetaSignals -> Kabroda.com
Author: Andy / Kabroda AI Brain
Purpose:
  Runs on Andy's dedicated Raspberry Pi (home network) 24/7.
  Listens to the Discord channel where MetaSignals alerts land (e.g. Mafioso_Majors),
  extracts the alert parameters (Symbol, Side, Entry, SL, TP1, TP2, Strategy),
  and forwards an authenticated HTTPS POST request to kabroda.com/api/metasignals/alert.

Why this air-gapped design?
  1. Protects kabroda.com and Render from Discord IP blocks or scraper flags.
  2. Ensures zero dependencies on Discord inside the production web app.

Installation on Raspberry Pi:
  sudo apt-get update && sudo apt-get install -y python3-pip
  pip3 install discord.py requests

Run as systemd service (/etc/systemd/system/metasignals-forwarder.service):
  [Unit]
  Description=MetaSignals Discord Alert Forwarder to Kabroda
  After=network.target

  [Service]
  Type=simple
  User=pi
  WorkingDirectory=/home/pi
  ExecStart=/usr/bin/python3 /home/pi/raspberry_pi_alert_forwarder.py
  Restart=always
  RestartSec=10
  Environment="KABRODA_URL=https://kabroda.com/api/metasignals/alert"
  Environment="KABRODA_INGEST_SECRET=YOUR_SECRET_TOKEN_HERE"
  Environment="DISCORD_USER_TOKEN=YOUR_DISCORD_TOKEN_HERE"
  Environment="DISCORD_CHANNEL_ID=TARGET_CHANNEL_ID_HERE"

  [Install]
  WantedBy=multi-user.target
=============================================================================
"""

import os
import re
import json
import logging
import requests
import discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

KABRODA_URL = os.getenv("KABRODA_URL", "https://kabroda.com/api/metasignals/alert")
KABRODA_SECRET = os.getenv("KABRODA_INGEST_SECRET", "YOUR_SECRET_TOKEN_HERE")
TARGET_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# Regex patterns matching MetaSignals Mafioso_Majors alerts
ALERT_PATTERN = re.compile(
    r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>[\d\.]+)\s*-\s*(?P<timeframe>[0-9A-Za-z]+)\s*-\s*(?P<strategy>[0-9A-Za-z\.\s]+)",
    re.IGNORECASE
)
SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>[\d\.]+)", re.IGNORECASE)
T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>[\d\.]+)", re.IGNORECASE)
T2_PATTERN = re.compile(r"Target 2:\s*(?P<tp2>[\d\.]+)", re.IGNORECASE)
T3_PATTERN = re.compile(r"Target 3:\s*(?P<tp3>[\d\.]+)", re.IGNORECASE)


class AlertClient(discord.Client):
    async def on_ready(self):
        logging.info(f"Connected to Discord as {self.user}")
        logging.info(f"Listening for alerts on Channel ID: {TARGET_CHANNEL_ID or 'ALL'}")
        logging.info(f"Forwarding destination: {KABRODA_URL}")

    async def on_message(self, message):
        # Ignore our own messages
        if message.author == self.user:
            return

        # Channel filter
        if TARGET_CHANNEL_ID != 0 and message.channel.id != TARGET_CHANNEL_ID:
            return

        # Extract text from plain content or embeds
        text = message.content or ""
        if message.embeds:
            for embed in message.embeds:
                if embed.title:
                    text += " " + embed.title
                if embed.description:
                    text += " " + embed.description
                if embed.fields:
                    for field in embed.fields:
                        text += f" {field.name}: {field.value}"

        if not text:
            return

        match = ALERT_PATTERN.search(text)
        if not match:
            return

        raw_symbol = match.group("symbol").upper()
        symbol_clean = f"{raw_symbol}USDT"
        entry_price = float(match.group("entry"))
        timeframe = match.group("timeframe").strip()
        strategy = match.group("strategy").strip()

        sl_match = SL_PATTERN.search(text)
        tp1_match = T1_PATTERN.search(text)
        tp2_match = T2_PATTERN.search(text)
        tp3_match = T3_PATTERN.search(text)

        if not sl_match:
            logging.warning(f"Matched header for {symbol_clean} but SL pattern missing. Skipping.")
            return

        soft_stop = float(sl_match.group("sl"))
        tp1 = float(tp1_match.group("tp1")) if tp1_match else round(entry_price * 1.04, 4)
        tp2 = float(tp2_match.group("tp2")) if tp2_match else round(entry_price * 1.08, 4)
        tp3 = float(tp3_match.group("tp3")) if tp3_match else None

        # Build clean JSON payload contract
        payload = {
            "raw_text": text,
            "source": "discord_raspberry_pi",
            "symbol": symbol_clean,
            "side": "BUY",
            "entry_price": entry_price,
            "soft_stop": soft_stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "timeframe": timeframe,
            "strategy": strategy,
            "timestamp": message.created_at.isoformat() if hasattr(message, "created_at") else None,
        }

        logging.info(f"Captured Valid Alert: {symbol_clean} @ ${entry_price:.4f} | SL: ${soft_stop:.4f} | Strategy: {strategy}")

        # Post to Kabroda.com
        try:
            resp = requests.post(
                KABRODA_URL,
                json=payload,
                headers={
                    "X-Kabroda-Secret": KABRODA_SECRET,
                    "Content-Type": "application/json",
                    "User-Agent": "Kabroda-Pi-Alert-Forwarder/1.0",
                },
                timeout=10,
            )
            logging.info(f"Forwarded to Kabroda: HTTP {resp.status_code} | Response: {resp.text}")
        except Exception as err:
            logging.error(f"Failed to forward alert to Kabroda: {err}")


if __name__ == "__main__":
    token = os.getenv("DISCORD_USER_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logging.error("FATAL: DISCORD_USER_TOKEN or DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)

    client = AlertClient()
    client.run(token)
