import re
with open('packages/core/src/modules/clubhouse/RoomsPanel.tsx', 'r') as f:
    lines = f.readlines()

div_count = 0
out = []
for i, line in enumerate(lines):
    if i < 779: continue
    if i > 2419: break
    
    divs_opened = len(re.findall(r'<div', line))
    divs_closed = len(re.findall(r'</div', line))
    
    indent = "  " * div_count
    
    div_count += divs_opened
    div_count -= divs_closed
    
    if divs_opened > 0 or divs_closed > 0:
        out.append(f"{i+1:4} | {indent}{line.strip()}")
        
with open('divs_indent.txt', 'w') as f:
    f.write('\n'.join(out))
