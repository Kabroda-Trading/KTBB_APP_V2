"""Find tab switch logic and polling mechanism."""
content = open('templates/suite_dashboard.html', encoding='utf-8').read()

# Find the tab switch logic
idx = content.find('document.querySelectorAll')
if idx == -1:
    idx = content.find('tab-btn')
if idx == -1:
    idx = content.find('data-tab')

if idx >= 0:
    with open('scratch/tab_logic.txt', 'w', encoding='utf-8') as f:
        f.write(content[idx:idx+3000])
    print(f"Found tab logic at {idx}")
else:
    print("Could not find tab logic!")

# Find any polling/interval mechanism
for term in ['setInterval', 'setTimeout', 'requestAnimationFrame', 'setInterval(']:
    idx = content.find(term)
    if idx >= 0:
        print(f"Found '{term}' at {idx}")
        with open(f'scratch/poll_{term}.txt', 'w', encoding='utf-8') as f:
            f.write(content[idx:idx+200])

# Check if there's a polling loop in the initDashboard override
idx = content.find('_origInit')
if idx >= 0:
    with open('scratch/override.txt', 'w', encoding='utf-8') as f:
        f.write(content[idx:idx+500])
    print(f"Found _origInit at {idx}")

# Check for any event listeners
idx = content.find('addEventListener')
if idx >= 0:
    with open('scratch/events.txt', 'w', encoding='utf-8') as f:
        f.write(content[idx:idx+2000])
    print(f"Found addEventListener at {idx}")
