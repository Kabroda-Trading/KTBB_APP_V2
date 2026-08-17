import json
data = json.load(open('extract/youtube_streams_analysis.json', encoding='utf-8'))
for d in data:
    print(f"=== {d['video_title']} ({d['highlights_count']} technical highlights) ===")
    for h in d['key_takeaways'][:6]:
        print(f"  [{h['keyword'].upper()}] {h['snippet']}")
    print()
