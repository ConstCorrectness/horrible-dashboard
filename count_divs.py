import re
with open('packages/core/src/modules/clubhouse/RoomsPanel.tsx', 'r') as f:
    lines = f.readlines()

div_count = 0
for i, line in enumerate(lines):
    if i < 779: continue
    if i > 2419: break
    
    divs_opened = len(re.findall(r'<div', line))
    divs_closed = len(re.findall(r'</div', line))
    
    div_count += divs_opened
    div_count -= divs_closed
    
    print(f"Line {i+1}: count {div_count} | {line.strip()}")
