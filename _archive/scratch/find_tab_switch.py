"""Find the tab switch and interval patterns."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()

# Find tab switch
idx = content.find('live-system')
# Search backwards for the tab switch logic
search_start = max(0, idx - 2000)
search_end = min(len(content), idx + 2000)
section = content[search_start:search_end]

# Write to file
with open('scratch/tab_switch_area.txt', 'w', encoding='utf-8') as f:
    f.write(section)

print(f"Wrote {len(section)} chars from position {search_start} to {search_end}")

# Also find setInterval
idx2 = content.find('setInterval')
with open('scratch/interval_area.txt', 'w', encoding='utf-8') as f:
    f.write(content[idx2:idx2+500])
print(f"setInterval at {idx2}")
