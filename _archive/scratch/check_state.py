"""Check the current state of the dashboard template."""
import re
from collections import Counter

content = open('templates/suite_dashboard.html', encoding='utf-8').read()

# Duplicate IDs
id_counts = Counter(re.findall(r'id="[a-zA-Z0-9_-]+"', content))
dupes = {k: v for k, v in id_counts.items() if v > 1}
print(f"Duplicate IDs: {len(dupes)}")
if dupes:
    for k, v in list(dupes.items())[:10]:
        print(f"  {k}: {v}")

# Div balance
opens = len(re.findall(r'<div\b[^>]*>', content))
closes = len(re.findall(r'</div>', content))
print(f"Div balance: opens={opens}, closes={closes}, balanced={opens == closes}")

# Function counts
for fn in ['loadSessionEnergy', 'loadHeartbeat', 'loadParameters', 'loadLiveSystem']:
    count = content.count(f'async function {fn}')
    print(f"{fn}: {count}")

# setInterval
print(f"setInterval count: {content.count('setInterval')}")

# File size
print(f"File size: {len(content)} chars")

# Check for the jewel_gate_open issue
print(f"\njewel_gate_open in endpoint: {'jewel_gate_open' in open('main.py', encoding='utf-8').read()}")
