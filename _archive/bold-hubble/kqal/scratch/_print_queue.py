import json
path = r'C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\bold-hubble\kqal\output\kabroda_improvement_queue.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)
for item in data.get('items', []):
    pid = item.get('id', '?')
    title = item.get('title', '?')
    deps = item.get('dependencies', [])
    prompt = item.get('claude_code_prompt', '')
    print(f"=== {pid}: {title} ===")
    print(f"  Dependencies: {deps}")
    print(f"  Prompt: {prompt[:200]}...")
    print()
