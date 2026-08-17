import json
import re
from bs4 import BeautifulSoup
import os

HTML_PATH = r"C:\Users\Shadow\.gemini\antigravity\brain\d770f0e6-62cc-4a9e-aa6c-6524765ed346\.system_generated\steps\6\content.md"

def build_map():
    if not os.path.exists(HTML_PATH):
        print(f"HTML file not found at {HTML_PATH}")
        return

    with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")
    sections = soup.find_all("div", class_="course-section")
    
    course_data = []
    
    # Exclude keywords
    exclude_keywords = ["options"]
    
    section_idx = 1
    for sec in sections:
        title_el = sec.find("div", class_="section-title")
        if not title_el:
            continue
        section_title = title_el.get_text(strip=True)
        
        # Clean section title (remove lock icon text etc.)
        section_title = re.sub(r"^\s+", "", section_title)
        
        if any(kw in section_title.lower() for kw in exclude_keywords):
            print(f"Skipping excluded section: {section_title}")
            continue
            
        lectures = []
        lecture_items = sec.find_all("li", class_="section-item")
        for item in lecture_items:
            link = item.find("a", class_="item")
            if not link:
                continue
            lecture_id = item.get("data-lecture-id")
            lecture_url = item.get("data-lecture-url")
            
            name_el = link.find("span", class_="lecture-name")
            raw_name = name_el.get_text(strip=True) if name_el else ""
            
            # Check duration if present e.g. (18:19)
            duration_match = re.search(r"\(\s*(\d+:\d+)\s*\)", raw_name)
            duration = duration_match.group(1) if duration_match else None
            
            clean_name = re.sub(r"\(\s*\d+:\d+\s*\)", "", raw_name).strip()
            
            # Check icon type (video vs quiz vs text)
            icon_use = link.find("use")
            icon_type = "video"
            if icon_use:
                href = icon_use.get("xlink:href", "")
                if "Quiz" in href:
                    icon_type = "quiz"
                elif "Subject" in href:
                    icon_type = "text"
            
            lectures.append({
                "id": lecture_id,
                "title": clean_name,
                "url": f"https://krown-trading.teachable.com{lecture_url}" if lecture_url else None,
                "duration": duration,
                "type": icon_type
            })
            
        course_data.append({
            "section_index": section_idx,
            "section_title": section_title,
            "lectures": lectures
        })
        section_idx += 1

    out_path = os.path.join(os.path.dirname(__file__), "course_map.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(course_data, f, indent=2)
    
    print(f"Course map written with {len(course_data)} sections and {sum(len(s['lectures']) for s in course_data)} lectures.")

if __name__ == "__main__":
    build_map()
