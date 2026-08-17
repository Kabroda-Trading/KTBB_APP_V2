"""Extract the Live System tab section from the dashboard template."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()
idx = content.find('tab-live-system')
if idx >= 0:
    section = content[idx:idx+4000]
    # Write to a file we can read
    with open('scratch/live_tab_section.html', 'w', encoding='utf-8') as f:
        f.write(section)
    print(f"Extracted {len(section)} chars starting at position {idx}")
else:
    print("tab-live-system not found")
