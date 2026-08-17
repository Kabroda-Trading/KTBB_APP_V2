"""Find the exact marker text after loadLiveSystem function."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()
idx = content.find('loadLiveSystem')
end = content.find('async function', idx + 20)
# Write the marker to a file
with open('scratch/marker_found.txt', 'w', encoding='utf-8') as f:
    f.write(repr(content[end:end+100]))
print(f"Marker starts at position {end}")
print("Written to scratch/marker_found.txt")
