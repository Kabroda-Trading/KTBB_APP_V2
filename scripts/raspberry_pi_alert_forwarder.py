#!/usr/bin/env python3
"""
raspberry_pi_alert_forwarder.py
=============================================================================
Raspberry Pi Air-Gapped Discord Listener for MetaSignals -> Kabroda.com
Author: Andy / Kabroda AI Brain

2026-10-13 (rewrite): parsing moved SERVER-SIDE (metasignals_triage.
parse_discord_alert(), KTBB_app_v2 repo) -- this script now forwards the
RAW Discord message (channel/author/content/embeds) to /api/metasignals/
webhook instead of running its own regex extraction and sending pre-
parsed trade fields. Reasoning: a regex fix for a new alert format used
to require re-deploying this file to physical Pi hardware; now it's a
server-side code change, redeployed the normal way, with the exact raw
message always preserved in metasignals_raw_alerts regardless of
whether the parser understood it. The OLD pre-parsed shape (entry_price/
soft_stop/tp1/tp2/...) is still accepted by the server as a legacy
fallback, but is no longer what this script sends.

Purpose:
  Runs on Andy's dedicated Raspberry Pi (home network) 24/7.
  Listens to the Discord channel where MetaSignals alerts land (e.g. Mafioso_Majors),
  and forwards every message's raw content/embeds via an authenticated HTTPS
  POST to kabroda.com/api/metasignals/webhook. No alert-format knowledge lives here.

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
  Environment="KABRODA_URL=https://kabroda.com/api/metasignals/webhook"
  Environment="KABRODA_SECRET_KEY=YOUR_SECRET_TOKEN_HERE"
  Environment="DISCORD_USER_TOKEN=YOUR_DISCORD_TOKEN_HERE"
  Environment="DISCORD_CHANNEL_ID=TARGET_CHANNEL_ID_HERE"

  [Install]
  WantedBy=multi-user.target
=============================================================================
"""

import os
import logging
import requests
import discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

KABRODA_URL = os.getenv("KABRODA_URL", "https://kabroda.com/api/metasignals/webhook")
KABRODA_SECRET = os.getenv("KABRODA_SECRET_KEY") or os.getenv("KABRODA_INGEST_SECRET", "YOUR_SECRET_TOKEN_HERE")
TARGET_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))


def _embed_to_dict(embed) -> dict:
    return {
        "title": embed.title,
        "description": embed.description,
        "fields": [{"name": f.name, "value": f.value} for f in (embed.fields or [])],
    }


class AlertClient(discord.Client):
    async def on_ready(self):
        logging.info(f"Connected to Discord as {self.user}")
        logging.info(f"Listening for alerts on Channel ID: {TARGET_CHANNEL_ID or 'ALL'}")
        logging.info(f"Forwarding destination: {KABRODA_URL}")

    async def on_message(self, message):
        # Ignore our own messages
        if message.author == self.user:
            return

        # Channel filter -- SCOPE only. No content-shape filtering here
        # at all: every message in the target channel is forwarded raw,
        # unconditionally. Whether it's a recognized trade alert is the
        # server's own decision (metasignals_triage.parse_discord_alert()),
        # and the server keeps the raw row regardless of its verdict --
        # zero data loss, not "the Pi decided this didn't matter."
        if TARGET_CHANNEL_ID != 0 and message.channel.id != TARGET_CHANNEL_ID:
            return

        if not message.content and not message.embeds:
            return   # a genuinely empty event (e.g. a message edit with nothing to show) -- nothing to forward

        payload = {
            "channel_id": str(message.channel.id),
            "channel_name": getattr(message.channel, "name", None),
            "message_id": str(message.id),
            "author": str(message.author),
            "content": message.content or "",
            "embeds": [_embed_to_dict(e) for e in message.embeds],
            "created_at": message.created_at.isoformat() if hasattr(message, "created_at") else None,
        }

        logging.info(f"Forwarding message {message.id} from #{payload['channel_name']} ({len(payload['content'])} chars, {len(payload['embeds'])} embeds)")

        try:
            resp = requests.post(
                KABRODA_URL,
                json=payload,
                headers={
                    "X-Kabroda-Secret": KABRODA_SECRET,
                    "Content-Type": "application/json",
                    "User-Agent": "Kabroda-Pi-Alert-Forwarder/2.0",
                },
                timeout=10,
            )
            logging.info(f"Forwarded to Kabroda: HTTP {resp.status_code} | Response: {resp.text}")
        except Exception as err:
            logging.error(f"Failed to forward message to Kabroda: {err}")


if __name__ == "__main__":
    token = os.getenv("DISCORD_USER_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logging.error("FATAL: DISCORD_USER_TOKEN or DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)

    client = AlertClient()
    client.run(token)
