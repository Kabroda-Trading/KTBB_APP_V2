"""Find the setInterval pattern in the dashboard."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()

# Find all setInterval calls
import re
for m in re.finditer(r'setInterval\([^)]+\)', content):
    print(f"  Position {m.start()}: {m.group()}")

# Also check the initDashboard end
idx = content.find('initDashboard()')
end = content.find('</script>', idx)
with open('scratch/init_full.txt', 'w', encoding='utf-8') as f:
    f.write(content[idx:end])
print(f"\nWrote initDashboard to scratch/init_full.txt ({end-idx} chars)")
