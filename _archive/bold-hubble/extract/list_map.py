import json
data = json.load(open('extract/course_map.json', encoding='utf-8'))
for s in data:
    print(f"{s['section_index']}. {s['section_title']} ({len(s['lectures'])} items)")
