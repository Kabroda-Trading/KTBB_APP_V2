"""Check exact marker text in the file."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()

# Check the live tab end marker
idx = content.find('TAB: PARAMETERS')
with open('scratch/marker_check.txt', 'w', encoding='utf-8') as f:
    f.write(repr(content[idx-300:idx+50]))

print(f"TAB: PARAMETERS at position {idx}")

# Check the initDashboard call
idx2 = content.find('loadLiveSystem().catch')
with open('scratch/init_check.txt', 'w', encoding='utf-8') as f:
    f.write(repr(content[idx2-50:idx2+200]))

print(f"loadLiveSystem().catch at position {idx2}")

# Check setInterval
idx3 = content.find('setInterval')
with open('scratch/interval_check.txt', 'w', encoding='utf-8') as f:
    f.write(repr(content[idx3:idx3+200]))

print(f"setInterval at position {idx3}")
