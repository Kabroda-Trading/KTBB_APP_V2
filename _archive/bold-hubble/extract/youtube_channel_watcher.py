#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ECKrown YouTube Channel Watcher Bot
====================================
Automatically discovers new videos from @ECKrown, downloads transcripts,
extracts structured trading signals, and outputs clean AI-readable files.

No YouTube API key required — uses RSS feed for discovery and
youtube-transcript-api for transcripts.

Output:
  - extract/output/signals/<video_id>.json   (structured signal data)
  - extract/output/reports/<video_id>.md     (human-readable report)
  - extract/output/daily/latest.md           (daily aggregated outlook)
  - extract/youtube_state.json               (tracks processed videos)

Usage:
  python extract/youtube_channel_watcher.py          # Process new videos
  python extract/youtube_channel_watcher.py --all    # Reprocess all videos
  python extract/youtube_channel_watcher.py --watch  # Continuous watch mode
"""

import os
import sys
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from urllib.request import urlopen, Request

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add parent to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHANNEL_ID = "UCnwxzpFzZNtLH8NgTeAROFA"  # @ECKrown
RSS_FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "youtube_state.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SIGNALS_DIR = os.path.join(OUTPUT_DIR, "signals")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
DAILY_DIR = os.path.join(OUTPUT_DIR, "daily")

# Krown-specific keywords for signal extraction
KROWN_KEYWORDS = [
    "bbwp", "pmarp", "revin ribbon", "revan ribbon", "ribbon midband",
    "midband", "gray dot", "lower band", "upper band",
    "ema", "sma", "squeeze", "divergence", "hidden bullish",
    "hidden bearish", "regular bullish", "regular bearish",
    "support", "resistance", "target", "stop loss", "take profit",
    "long", "short", "bullish", "bearish", "bias",
    "60k", "58k", "55k", "62k", "65k", "70k",
    "strategy", "entry", "confirmation", "reclaim",
    "volatility", "compression", "expansion", "blow-off",
    "overextended", "capitulation", "discount",
    "value zone", "golden pocket", "fibonacci", "fib",
    "momentum", "trend", "structure", "risk",
]

# Price pattern: $XX,XXX or $XX.XX or XX,XXX
PRICE_PATTERN = re.compile(r'\$?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*(k|K)?')
# Target pattern: "target of/about/at $XX,XXX"
TARGET_PATTERN = re.compile(r'(?:target|aiming|heading|next\s*stop|looking\s*for)\s*(?:of|at|about|is|:)?\s*\$?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*k)?', re.IGNORECASE)
# Support/Resistance pattern
LEVEL_PATTERN = re.compile(r'(support|resistance|resistance\s*level|support\s*level|key\s*level|pivot)\s*(?:at|of|is|:)?\s*\$?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*k)?', re.IGNORECASE)
# Bias pattern
BIAS_PATTERN = re.compile(r'(short-term|medium-term|long-term|short\s*term|medium\s*term|long\s*term|immediate)\s*(?:bearish|bullish|neutral|bias)', re.IGNORECASE)
# BBWP pattern
BBWP_PATTERN = re.compile(r'bbwp\s*(?:is|at|reading|showing|sitting)?\s*(\d+\.?\d*)\s*%?', re.IGNORECASE)
# PMARP pattern
PMARP_PATTERN = re.compile(r'pmarp\s*(?:is|at|reading|showing|sitting)?\s*(\d+\.?\d*)\s*%?', re.IGNORECASE)
# RSI pattern
RSI_PATTERN = re.compile(r'rsi\s*(?:is|at|reading|showing)?\s*(\d+\.?\d*)', re.IGNORECASE)
# Revin Ribbons midband pattern
MIDBAND_PATTERN = re.compile(r'(?:revin|revan)\s*ribbons?\s*(?:midband|mid\s*band|mid-line|midline)\s*(?:is|at|of|:)?\s*\$?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*k)?', re.IGNORECASE)
# Asset pattern
ASSET_PATTERN = re.compile(r'\b(BTC|Bitcoin|ETH|Ethereum|SOL|Solana|SPY|SPX|S&P|Nasdaq|QQQ|Gold|Silver|DXY|USD)\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def load_state() -> Dict[str, Any]:
    """Load processed video state from disk."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_ids": [], "last_check": None, "videos": {}}


def save_state(state: Dict[str, Any]):
    """Save processed video state to disk."""
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# RSS Feed Discovery
# ---------------------------------------------------------------------------

def fetch_rss_feed(url: str) -> Optional[str]:
    """Fetch YouTube RSS feed content."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [ERROR] Failed to fetch RSS feed: {e}")
        return None


def parse_rss_feed(xml_content: str) -> List[Dict[str, str]]:
    """Parse YouTube RSS feed XML into video entries."""
    videos = []
    try:
        root = ET.fromstring(xml_content)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "yt": "http://www.youtube.com/xml/schemas/2015",
              "media": "http://search.yahoo.com/mrss/"}

        for entry in root.findall("atom:entry", ns):
            video_id = entry.find("yt:videoId", ns)
            title = entry.find("atom:title", ns)
            published = entry.find("atom:published", ns)
            link = entry.find("atom:link", ns)
            author = entry.find("atom:author", ns)

            if video_id is not None:
                videos.append({
                    "id": video_id.text,
                    "title": title.text.strip() if title is not None else "Unknown",
                    "published": published.text if published is not None else "",
                    "url": f"https://youtu.be/{video_id.text}",
                    "author": author.find("atom:name", ns).text if author is not None else "ECKrown",
                })
    except Exception as e:
        print(f"  [ERROR] Failed to parse RSS feed: {e}")

    return videos


def discover_new_videos(state: Dict[str, Any]) -> List[Dict[str, str]]:
    """Fetch RSS feed and return only unprocessed videos."""
    print(f"[RSS] Fetching feed: {RSS_FEED_URL}")
    xml_content = fetch_rss_feed(RSS_FEED_URL)
    if not xml_content:
        return []

    all_videos = parse_rss_feed(xml_content)
    processed_ids = set(state.get("processed_ids", []))

    new_videos = [v for v in all_videos if v["id"] not in processed_ids]
    print(f"[RSS] Found {len(all_videos)} total videos, {len(new_videos)} new")

    return new_videos


# ---------------------------------------------------------------------------
# Transcript Download
# ---------------------------------------------------------------------------

def download_transcript(video_id: str) -> Optional[str]:
    """Download YouTube transcript using youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        try:
            transcript = api.fetch(video_id)
        except Exception:
            try:
                transcript_list = api.list(video_id)
                transcript = transcript_list.find_transcript(["en"]).fetch()
            except Exception:
                # Try any available transcript
                try:
                    transcript_list = api.list(video_id)
                    transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"]).fetch()
                except Exception:
                    return None

        # Combine into full text with timestamps
        segments = []
        for item in transcript:
            segments.append({
                "text": item.text,
                "start": item.start,
                "duration": item.duration,
            })

        full_text = " ".join([s["text"] for s in segments])
        return full_text

    except ImportError:
        print("  [ERROR] youtube-transcript-api not installed. Run: pip install youtube-transcript-api")
        return None
    except Exception as e:
        print(f"  [ERROR] Transcript download failed for {video_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Signal Extraction
# ---------------------------------------------------------------------------

def extract_signals(video_id: str, title: str, transcript: str) -> Dict[str, Any]:
    """Extract structured trading signals from transcript text."""
    signals = {
        "video_id": video_id,
        "video_title": title,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "word_count": len(transcript.split()),
        "market_bias": extract_bias(transcript),
        "key_levels": extract_key_levels(transcript),
        "indicators": extract_indicators(transcript),
        "active_strategies": extract_strategies(transcript),
        "assets_mentioned": extract_assets(transcript),
        "key_snippets": extract_key_snippets(transcript),
        "summary": generate_summary(transcript),
    }
    return signals


def extract_bias(text: str) -> Dict[str, str]:
    """Extract market bias per timeframe."""
    bias = {"short_term": "neutral", "medium_term": "neutral", "long_term": "neutral"}

    # Short-term bias
    st_bullish = len(re.findall(r'(?:short-term|short\s*term).{0,50}bullish', text, re.IGNORECASE))
    st_bearish = len(re.findall(r'(?:short-term|short\s*term).{0,50}bearish', text, re.IGNORECASE))
    if st_bullish > st_bearish:
        bias["short_term"] = "bullish"
    elif st_bearish > st_bullish:
        bias["short_term"] = "bearish"

    # Medium-term bias
    mt_bullish = len(re.findall(r'(?:medium-term|medium\s*term).{0,50}bullish', text, re.IGNORECASE))
    mt_bearish = len(re.findall(r'(?:medium-term|medium\s*term).{0,50}bearish', text, re.IGNORECASE))
    if mt_bullish > mt_bearish:
        bias["medium_term"] = "bullish"
    elif mt_bearish > mt_bullish:
        bias["medium_term"] = "bearish"

    # Long-term bias
    lt_bullish = len(re.findall(r'(?:long-term|long\s*term).{0,50}bullish', text, re.IGNORECASE))
    lt_bearish = len(re.findall(r'(?:long-term|long\s*term).{0,50}bearish', text, re.IGNORECASE))
    if lt_bullish > lt_bearish:
        bias["long_term"] = "bullish"
    elif lt_bearish > lt_bullish:
        bias["long_term"] = "bearish"

    # Fallback: check for general bullish/bearish sentiment
    if bias["short_term"] == "neutral":
        bullish_count = len(re.findall(r'\bbullish\b', text, re.IGNORECASE))
        bearish_count = len(re.findall(r'\bbearish\b', text, re.IGNORECASE))
        if bullish_count > bearish_count + 2:
            bias["short_term"] = "bullish"
        elif bearish_count > bullish_count + 2:
            bias["short_term"] = "bearish"

    return bias


def extract_key_levels(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Extract price levels: support, resistance, targets."""
    levels = {"support": [], "resistance": [], "targets": [], "midband": []}

    # Targets
    for match in TARGET_PATTERN.finditer(text):
        price = match.group(1).replace(",", "")
        try:
            levels["targets"].append({
                "price": float(price),
                "context": text[max(0, match.start()-30):match.end()+30].strip()
            })
        except ValueError:
            pass

    # Support/Resistance
    for match in LEVEL_PATTERN.finditer(text):
        level_type = match.group(1).lower()
        price = match.group(2).replace(",", "")
        try:
            key = "support" if "support" in level_type else "resistance"
            levels[key].append({
                "price": float(price),
                "context": text[max(0, match.start()-30):match.end()+30].strip()
            })
        except ValueError:
            pass

    # Revin Ribbons midband
    for match in MIDBAND_PATTERN.finditer(text):
        price = match.group(1).replace(",", "")
        try:
            levels["midband"].append({
                "price": float(price),
                "context": text[max(0, match.start()-30):match.end()+30].strip()
            })
        except ValueError:
            pass

    # Deduplicate and sort
    for key in levels:
        seen = set()
        unique = []
        for item in levels[key]:
            p = item["price"]
            if p not in seen:
                seen.add(p)
                unique.append(item)
        unique.sort(key=lambda x: x["price"])
        levels[key] = unique

    return levels


def extract_indicators(text: str) -> Dict[str, Any]:
    """Extract indicator readings from transcript."""
    indicators = {}

    # BBWP
    bbwp_matches = BBWP_PATTERN.findall(text)
    if bbwp_matches:
        try:
            val = float(bbwp_matches[-1])
            if val <= 5.0:
                state = "extreme_squeeze"
            elif val <= 15.0:
                state = "moderate_squeeze"
            elif val >= 95.0:
                state = "extreme_exhaustion"
            elif val >= 85.0:
                state = "high_expansion"
            else:
                state = "normal"
            indicators["bbwp"] = {"value": val, "state": state}
        except ValueError:
            pass

    # PMARP
    pmarp_matches = PMARP_PATTERN.findall(text)
    if pmarp_matches:
        try:
            val = float(pmarp_matches[-1])
            if val >= 95.0:
                state = "overextended_top"
            elif val <= 5.0:
                state = "capitulation_discount"
            else:
                state = "normal"
            indicators["pmarp"] = {"value": val, "state": state}
        except ValueError:
            pass

    # RSI
    rsi_matches = RSI_PATTERN.findall(text)
    if rsi_matches:
        try:
            val = float(rsi_matches[-1])
            if val >= 70:
                state = "overbought"
            elif val <= 30:
                state = "oversold"
            else:
                state = "neutral"
            indicators["rsi"] = {"value": val, "state": state}
        except ValueError:
            pass

    # Divergences
    divergences = []
    for div_type in ["regular bullish", "regular bearish", "hidden bullish", "hidden bearish"]:
        count = len(re.findall(div_type, text, re.IGNORECASE))
        if count > 0:
            divergences.append({"type": div_type, "count": count})
    if divergences:
        indicators["divergences"] = divergences

    # Revin Ribbons
    if re.search(r'(?:revin|revan)\s*ribbons?', text, re.IGNORECASE):
        indicators["revin_ribbons"] = {"mentioned": True}
        if re.search(r'below\s+(?:the\s+)?(?:revin|revan)\s*ribbons?\s*midband', text, re.IGNORECASE):
            indicators["revin_ribbons"]["position"] = "below_midband"
        elif re.search(r'above\s+(?:the\s+)?(?:revin|revan)\s*ribbons?\s*midband', text, re.IGNORECASE):
            indicators["revin_ribbons"]["position"] = "above_midband"

    # Volatility state
    if re.search(r'(?:volatility|vol)\s*(?:squeeze|compression|compressing)', text, re.IGNORECASE):
        indicators["volatility_state"] = "compressing"
    elif re.search(r'(?:volatility|vol)\s*(?:expansion|expanding|blow.?off)', text, re.IGNORECASE):
        indicators["volatility_state"] = "expanding"

    return indicators


def extract_strategies(text: str) -> List[Dict[str, Any]]:
    """Identify which of Krown's 5 strategies are mentioned."""
    strategies = []
    strategy_map = {
        "strategy_1": ["macro trend", "trend breakout", "strategy #1", "strategy 1"],
        "strategy_2": ["uptrend pullback", "dip buy", "pullback long", "strategy #2", "strategy 2"],
        "strategy_3": ["downtrend continuation", "rally sell", "continuation short", "strategy #3", "strategy 3"],
        "strategy_4": ["parabolic exhaustion", "blow-off", "counter-trend", "strategy #4", "strategy 4"],
        "strategy_5": ["momentum breakdown", "support collapse", "breakdown short", "strategy #5", "strategy 5"],
    }

    for strat_id, keywords in strategy_map.items():
        for kw in keywords:
            if re.search(kw, text, re.IGNORECASE):
                strategies.append({"strategy": strat_id, "trigger": kw, "mentioned": True})
                break

    return strategies


def extract_assets(text: str) -> List[str]:
    """Extract mentioned trading assets."""
    assets = set()
    for match in ASSET_PATTERN.finditer(text):
        asset = match.group(1).upper()
        # Normalize
        if asset in ("BITCOIN",):
            asset = "BTC"
        elif asset in ("ETHEREUM",):
            asset = "ETH"
        elif asset in ("SOLANA",):
            asset = "SOL"
        elif asset in ("S&P", "SPX"):
            asset = "SPX"
        assets.add(asset)
    return sorted(list(assets))


def extract_key_snippets(text: str) -> List[Dict[str, str]]:
    """Extract key trading snippets around important keywords."""
    snippets = []
    for kw in KROWN_KEYWORDS:
        matches = list(re.finditer(kw, text, re.IGNORECASE))
        for m in matches[:2]:  # Max 2 per keyword
            start = max(0, m.start() - 100)
            end = min(len(text), m.end() + 200)
            snippet = text[start:end].replace("\n", " ").strip()
            snippets.append({
                "keyword": kw,
                "snippet": f"...{snippet}..."
            })
    # Limit total snippets
    return snippets[:30]


def generate_summary(text: str) -> str:
    """Generate a brief summary of the video's trading content."""
    # Count key signal words
    bullish_count = len(re.findall(r'\bbullish\b', text, re.IGNORECASE))
    bearish_count = len(re.findall(r'\bbearish\b', text, re.IGNORECASE))
    target_count = len(TARGET_PATTERN.findall(text))
    level_count = len(LEVEL_PATTERN.findall(text))

    # Determine dominant theme
    if bullish_count > bearish_count + 3:
        sentiment = "bullish-leaning"
    elif bearish_count > bullish_count + 3:
        sentiment = "bearish-leaning"
    else:
        sentiment = "neutral"

    # Check for key topics
    topics = []
    if re.search(r'bbwp|squeeze|compression|expansion', text, re.IGNORECASE):
        topics.append("volatility analysis")
    if re.search(r'divergence', text, re.IGNORECASE):
        topics.append("divergence analysis")
    if re.search(r'(?:revin|revan)\s*ribbons?', text, re.IGNORECASE):
        topics.append("Revin Ribbons")
    if re.search(r'support|resistance', text, re.IGNORECASE):
        topics.append("key levels")
    if re.search(r'strategy|entry|stop|target', text, re.IGNORECASE):
        topics.append("trade setups")

    summary = f"Sentiment: {sentiment}. "
    summary += f"Covers {', '.join(topics)}. "
    summary += f"Mentions {target_count} price targets and {level_count} support/resistance levels."

    return summary


# ---------------------------------------------------------------------------
# Output Writers
# ---------------------------------------------------------------------------

def write_signal_json(video_id: str, signals: Dict[str, Any]):
    """Write structured signal data as JSON."""
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    path = os.path.join(SIGNALS_DIR, f"{video_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(signals, f, indent=2, default=str)
    print(f"  [OUTPUT] Signal JSON: {path}")


def write_markdown_report(video_id: str, title: str, signals: Dict[str, Any]):
    """Write a human-readable markdown report."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{video_id}.md")

    bias = signals.get("market_bias", {})
    indicators = signals.get("indicators", {})
    levels = signals.get("key_levels", {})
    strategies = signals.get("active_strategies", [])
    assets = signals.get("assets_mentioned", [])

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"**Video**: [{title}](https://youtu.be/{video_id})\n\n")
        f.write(f"**Extracted**: {signals.get('extracted_at', 'N/A')}\n")
        f.write(f"**Word Count**: {signals.get('word_count', 0)}\n\n")

        f.write("## Market Bias\n\n")
        f.write(f"| Timeframe | Bias |\n")
        f.write(f"|-----------|------|\n")
        for tf in ["short_term", "medium_term", "long_term"]:
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
            f.write(f"| {tf.replace('_', ' ').title()} | {emoji.get(bias.get(tf, 'neutral'), '⚪')} {bias.get(tf, 'neutral').title()} |\n")
        f.write("\n")

        if assets:
            f.write("## Assets Mentioned\n\n")
            f.write(", ".join([f"`{a}`" for a in assets]) + "\n\n")

        if indicators:
            f.write("## Indicator Readings\n\n")
            if "bbwp" in indicators:
                bbwp = indicators["bbwp"]
                f.write(f"- **BBWP**: {bbwp.get('value', 'N/A')}% — *{bbwp.get('state', 'N/A').replace('_', ' ').title()}*\n")
            if "pmarp" in indicators:
                pmarp = indicators["pmarp"]
                f.write(f"- **PMARP**: {pmarp.get('value', 'N/A')}% — *{pmarp.get('state', 'N/A').replace('_', ' ').title()}*\n")
            if "rsi" in indicators:
                rsi = indicators["rsi"]
                f.write(f"- **RSI**: {rsi.get('value', 'N/A')} — *{rsi.get('state', 'N/A').title()}*\n")
            if "revin_ribbons" in indicators:
                rr = indicators["revin_ribbons"]
                pos = rr.get("position", "mentioned")
                f.write(f"- **Revin Ribbons**: {pos.replace('_', ' ').title()}\n")
            if "volatility_state" in indicators:
                f.write(f"- **Volatility**: {indicators['volatility_state'].title()}\n")
            if "divergences" in indicators:
                for d in indicators["divergences"]:
                    f.write(f"- **Divergence**: {d['type'].title()} (x{d['count']})\n")
            f.write("\n")

        if levels:
            f.write("## Key Price Levels\n\n")
            for level_type in ["support", "resistance", "targets", "midband"]:
                items = levels.get(level_type, [])
                if items:
                    label = level_type.replace("_", " ").title()
                    f.write(f"### {label}\n\n")
                    for item in items[:5]:
                        f.write(f"- **${item['price']:,.0f}**\n")
                    f.write("\n")

        if strategies:
            f.write("## Active Strategies\n\n")
            for s in strategies:
                f.write(f"- {s['strategy'].replace('_', ' ').title()}\n")
            f.write("\n")

        f.write("## Summary\n\n")
        f.write(signals.get("summary", "No summary available.") + "\n\n")

        f.write("---\n")
        f.write(f"*Generated by ECKrown YouTube Watcher Bot*\n")

    print(f"  [OUTPUT] Report: {path}")


def update_daily_outlook(signals: List[Dict[str, Any]]):
    """Aggregate today's signals into a daily outlook."""
    os.makedirs(DAILY_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(DAILY_DIR, f"{today}.md")
    latest_path = os.path.join(DAILY_DIR, "latest.md")

    if not signals:
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# ECKrown Daily Market Outlook — {today}\n\n")
        f.write(f"**Videos analyzed**: {len(signals)}\n\n")

        for sig in signals:
            f.write(f"## [{sig['video_title']}](https://youtu.be/{sig['video_id']})\n\n")
            f.write(f"{sig.get('summary', '')}\n\n")
            bias = sig.get("market_bias", {})
            f.write(f"**Bias**: ST {bias.get('short_term', 'N/A')} | MT {bias.get('medium_term', 'N/A')} | LT {bias.get('long_term', 'N/A')}\n\n")
            f.write("---\n\n")

    # Copy to latest
    import shutil
    shutil.copy2(path, latest_path)
    print(f"  [OUTPUT] Daily outlook: {path}")


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def process_video(video: Dict[str, str], state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single video: download transcript, extract signals, write output."""
    video_id = video["id"]
    title = video["title"]
    print(f"\n{'='*60}")
    print(f"[PROCESS] {title}")
    print(f"          https://youtu.be/{video_id}")

    # Download transcript
    print(f"  [TRANSCRIPT] Downloading...")
    transcript = download_transcript(video_id)
    if not transcript:
        print(f"  [SKIP] No transcript available for {video_id}")
        return None

    print(f"  [TRANSCRIPT] {len(transcript.split())} words downloaded")

    # Extract signals
    print(f"  [SIGNALS] Extracting trading intelligence...")
    signals = extract_signals(video_id, title, transcript)

    # Write outputs
    write_signal_json(video_id, signals)
    write_markdown_report(video_id, title, signals)

    # Mark as processed
    state["processed_ids"].append(video_id)
    state["videos"][video_id] = {
        "title": title,
        "url": video["url"],
        "published": video.get("published", ""),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "word_count": signals["word_count"],
    }

    return signals


def run_pipeline(process_all: bool = False, watch_mode: bool = False):
    """Main pipeline execution."""
    print(f"\n{'#'*60}")
    print(f"# ECKrown YouTube Channel Watcher")
    print(f"# Channel: @ECKrown ({CHANNEL_ID})")
    print(f"{'#'*60}\n")

    state = load_state()
    print(f"[STATE] Previously processed: {len(state.get('processed_ids', []))} videos")

    # Discover new videos
    new_videos = discover_new_videos(state)

    if process_all:
        # Get ALL videos from feed
        xml_content = fetch_rss_feed(RSS_FEED_URL)
        if xml_content:
            all_videos = parse_rss_feed(xml_content)
            print(f"[BULK] Processing all {len(all_videos)} videos from feed...")
            new_videos = all_videos  # Reprocess all

    if not new_videos:
        print("\n[INFO] No new videos to process.")
        if watch_mode:
            print("[WATCH] Will check again in 30 minutes...")
        return

    # Process each new video
    all_signals = []
    for video in new_videos:
        signals = process_video(video, state)
        if signals:
            all_signals.append(signals)

    # Update daily outlook
    if all_signals:
        update_daily_outlook(all_signals)

    # Save state
    save_state(state)
    print(f"\n[DONE] Processed {len(all_signals)} new videos")
    print(f"[STATE] Total processed: {len(state.get('processed_ids', []))} videos")

    if watch_mode:
        print("[WATCH] Will check again in 30 minutes...")
        time.sleep(1800)  # 30 minutes
        run_pipeline(process_all=False, watch_mode=True)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ECKrown YouTube Channel Watcher Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract/youtube_channel_watcher.py          # Process new videos only
  python extract/youtube_channel_watcher.py --all     # Reprocess all videos from feed
  python extract/youtube_channel_watcher.py --watch   # Continuous watch mode (checks every 30min)
  python extract/youtube_channel_watcher.py --id VIDEO_ID  # Process a specific video by ID
        """
    )
    parser.add_argument("--all", action="store_true", help="Reprocess all videos from RSS feed")
    parser.add_argument("--watch", action="store_true", help="Continuous watch mode (checks every 30 min)")
    parser.add_argument("--id", type=str, help="Process a specific video by YouTube ID")

    args = parser.parse_args()

    if args.id:
        # Process a single video by ID
        state = load_state()
        video = {
            "id": args.id,
            "title": f"Video {args.id}",
            "url": f"https://youtu.be/{args.id}",
            "published": "",
        }
        signals = process_video(video, state)
        if signals:
            update_daily_outlook([signals])
            save_state(state)
    else:
        run_pipeline(process_all=args.all, watch_mode=args.watch)
