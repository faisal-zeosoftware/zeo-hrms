"""Ask ZEO 1.72.0 – the product knowledge: the package's guides (INSTALL, DEVELOPER-SETUP, README-*.md) cut into sections and saved as
AskZeo/data/product_docs.json, so Ask ZEO can answer "can ZEO do …", "how do we set up / implement …" and scope questions on any
server (the package folder is not on the server, only zeo-hrms). Run when the guides change:

    python3 zeo-hrms/AskZeo/tools/export_docs.py <package folder>
"""
import json
import os
import re
import sys

pkg = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '../../..'))
files = ['README-UPDATE.md', 'INSTALL.md', 'README-PROJECTS.md', 'README-HSE.md', 'README-CRM-PROPERTY-MAINTENANCE.md', 'README-MOBILE.md']
files += sorted(f for f in os.listdir(pkg) if f.startswith('DEVELOPER-SETUP') and f.endswith('.md'))
files += sorted(f for f in os.listdir(pkg) if f.startswith('README-HSE-SCOPE') and f.endswith('.md')) + ['DEVELOPER-MODULES.md']   # v1.70.4: the guides of 1.70.2 and 1.70.3
out = []
for f in files:
    p = os.path.join(pkg, f)
    if not os.path.exists(p):
        continue
    title, path, buf = '', [], []

    def flush():
        body = '\n'.join(buf).strip()
        body = re.sub(r'\n{3,}', '\n\n', body)
        if len(body) > 40:
            for i in range(0, len(body), 1800):
                out.append({'doc': f, 'section': ' › '.join(path) or f, 'text': body[i:i + 1800]})
    for line in open(p, encoding='utf-8'):
        m = re.match(r'^(#{1,4})\s+(.*)', line)
        if m:
            flush()
            buf = []
            lvl = len(m.group(1))
            path = path[:lvl - 1] + [m.group(2).strip()]
            continue
        buf.append(line.rstrip())
    flush()
dest = os.path.join(os.path.dirname(__file__), '..', 'data', 'product_docs.json')
json.dump({'sections': out}, open(dest, 'w', encoding='utf-8'), ensure_ascii=False)
print(len(out), 'sections from', len(files), 'guides')
