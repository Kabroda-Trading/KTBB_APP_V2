import requests
import re
import os
import urllib.parse

def download_vtt_from_m3u8(m3u8_url, output_path):
    print(f"Fetching m3u8 playlist: {m3u8_url[:80]}...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://player.hotmart.com/"
    }
    
    resp = requests.get(m3u8_url, headers=headers)
    if resp.status_code != 200:
        print(f"Failed to fetch m3u8: {resp.status_code}")
        return False
        
    lines = resp.text.splitlines()
    base_url = m3u8_url.split("?")[0].rsplit("/", 1)[0] + "/"
    query_params = "?" + m3u8_url.split("?")[1] if "?" in m3u8_url else ""
    
    segment_urls = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            if line.startswith("http"):
                segment_urls.append(line)
            else:
                segment_urls.append(base_url + line + query_params)
                
    print(f"Found {len(segment_urls)} VTT segments. Downloading and stitching...")
    
    full_text = []
    seen_lines = set()
    
    for idx, seg_url in enumerate(segment_urls):
        seg_resp = requests.get(seg_url, headers=headers)
        if seg_resp.status_code == 200:
            seg_lines = seg_resp.text.splitlines()
            for sline in seg_lines:
                sline = sline.strip()
                # skip webvtt header, timestamps, metadata identifiers (digits)
                if not sline or sline == "WEBVTT" or sline.startswith("X-TIMESTAMP") or "-->" in sline or sline.isdigit():
                    continue
                # deduplicate continuous lines across boundaries if needed
                if sline not in seen_lines or (full_text and full_text[-1] != sline):
                    full_text.append(sline)
                    seen_lines.add(sline)
        else:
            print(f"Warning: failed segment {idx}: {seg_resp.status_code}")
            
    clean_transcript = " ".join(full_text)
    # Clean up excess spaces
    clean_transcript = re.sub(r'\s+', ' ', clean_transcript).strip()
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Moving Averages (MA's) - Video Transcript\n\n")
        f.write(clean_transcript + "\n")
        
    print(f"Successfully downloaded transcript to {output_path} ({len(clean_transcript)} characters)")
    return True

if __name__ == "__main__":
    m3u8 = "https://vod-akm.play.hotmart.com/video/DLN00Kg6Rr/hls/DLN00Kg6Rr-1708409618000-textstream_eng=1000.m3u8?hdntl=exp=1782950770~acl=/*~data=hdntl~hmac=20d5a5100400f49c7418c13bee38bf27dd0c83e7698ffef3be25bf616821dfb9&app=aa2d356b-e2f0-45e8-9725-e0efc7b5d29c"
    out = os.path.join(os.path.dirname(__file__), "output", "03_indicators", "01_moving_averages.md")
    download_vtt_from_m3u8(m3u8, out)
