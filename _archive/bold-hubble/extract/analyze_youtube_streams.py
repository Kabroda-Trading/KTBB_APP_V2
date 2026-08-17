import re
import os
import json
from youtube_transcript_api import YouTubeTranscriptApi

VIDEOS = [
    {"url": "https://youtu.be/SSg1qaJxthY", "id": "SSg1qaJxthY", "title": "Bitcoin Just Reclaimed $60K | Here's What Happens Next (July 1)"},
    {"url": "https://www.youtube.com/live/b6H3v0xk_Vg", "id": "b6H3v0xk_Vg", "title": "Bitcoin Hovers Below $60K as S&P 500 Prints Best Quarter (June 30)"},
    {"url": "https://www.youtube.com/watch?v=g8691YlCGuY", "id": "g8691YlCGuY", "title": "While Retail Panics on Bitcoin, I'm Loading Up On These 2 Q3 Stocks (June 29)"},
    {"url": "https://www.youtube.com/live/733ZY84UMaU", "id": "733ZY84UMaU", "title": "Micron's Record Earnings Couldn't Save the AI Trade (June 26)"},
    {"url": "https://www.youtube.com/watch?v=Rr5WsrnSCnA", "id": "Rr5WsrnSCnA", "title": "Hot Inflation Just Hit, Bitcoin's at Yearly Lows (June 25)"},
    {"url": "https://youtube.com/live/8mqRXj5PkBw", "id": "8mqRXj5PkBw", "title": "Bitcoin, Chips, and Gold Are All Falling Together (June 24)"},
    {"url": "https://youtu.be/jKfPj-_OC34", "id": "jKfPj-_OC34", "title": "Chips Just Got Routed, Bitcoin Hit My First Target (June 23)"},
]

def analyze_streams():
    print("Fetching YouTube transcripts for Krown's recent Discord streams...")
    output_data = []
    
    keywords = ["bbwp", "pmarp", "ribbon", "ribbons", "ema", "sma", "squeeze", "target", "support", "resistance", "divergence", "60k", "58k", "55k", "62k", "65k", "long", "short"]
    
    for v in VIDEOS:
        vid_id = v["id"]
        print(f"Processing: {v['title']} ({vid_id})...")
        try:
            api = YouTubeTranscriptApi()
            try:
                t = api.fetch(vid_id)
                transcript_list = t
            except Exception:
                t_list = api.list(vid_id)
                t = t_list.find_transcript(['en'])
                transcript_list = t.fetch()
        except Exception as e:
            print(f"  Could not fetch transcript for {vid_id}: {e}")
            continue
                
        full_text = " ".join([item.text for item in transcript_list])
        
        # Search for key technical segments
        highlights = []
        for kw in keywords:
            matches = [m.start() for m in re.finditer(kw, full_text, re.IGNORECASE)]
            for pos in matches[:3]: # take first 3 mentions per keyword
                start = max(0, pos - 120)
                end = min(len(full_text), pos + 250)
                snippet = full_text[start:end].replace('\n', ' ')
                highlights.append({
                    "keyword": kw,
                    "snippet": f"...{snippet}..."
                })
                
        output_data.append({
            "video_title": v["title"],
            "video_url": v["url"],
            "word_count": len(full_text.split()),
            "highlights_count": len(highlights),
            "key_takeaways": highlights[:15]  # Keep top 15 relevant snippets
        })
        
    out_path = os.path.join(os.path.dirname(__file__), "youtube_streams_analysis.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
        
    print(f"Analyzed {len(output_data)} videos. Results written to {out_path}")

if __name__ == "__main__":
    analyze_streams()
