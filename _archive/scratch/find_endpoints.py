"""Find all API endpoints in main.py."""
import re

content = open('main.py', encoding='utf-8').read()
endpoints = re.findall(r'@(?:router|app)\.(?:get|post)\s*\([\'"]([^\'"]+)[\'"]', content)
for e in sorted(endpoints):
    print(e)
