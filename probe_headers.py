#!/usr/bin/env python3
"""Diagnose: Wie sehen Seitenköpfe in Band 4-6 aus (Vocab-Seiten)?"""
import re
import sys

BAND = sys.argv[1] if len(sys.argv) > 1 else '4'
lines = [l.rstrip('\n').rstrip('\r') for l in open(f'import/Access_G9_Band{BAND}_text.txt', encoding='utf-8', errors='replace')]

PAGE_RE = re.compile(r'^=== Seite (\d+) ===\s*$')
pages = []
for i, l in enumerate(lines):
    m = PAGE_RE.match(l.strip())
    if m:
        pages.append((i, int(m.group(1))))

shown = 0
for idx, (i, num) in enumerate(pages):
    end = pages[idx + 1][0] if idx + 1 < len(pages) else len(lines)
    body = [x.strip() for x in lines[i + 1:end] if x.strip()]
    if not body:
        continue
    first = body[0]
    # Seiten mit 'Vocabulary' irgendwo in den ersten 3 Zeilen
    if any('Vocabulary' in x or x.startswith('Unit ') for x in body[:3]):
        print(f'--- Buchseite {num}: first3: {body[:3]!r}')
        shown += 1
        if shown >= 30:
            break