#!/usr/bin/env python3
"""Diagnose: Wo hat das PDF Textlayer?"""
import sys
import pymupdf

path = sys.argv[1] if len(sys.argv) > 1 else 'import/Access_G9_Band1_5Schuljahr.pdf'
doc = pymupdf.open(path)
print('Seiten:', len(doc))

hits = []
empty = 0
for i in range(len(doc)):
    try:
        t = doc[i].get_text().strip()
    except Exception:
        t = ''
    if t:
        if 'Vocabulary' in t:
            hits.append(i)
    else:
        empty += 1

print(f'Seiten mit Text: {len(doc)-empty}, leer: {empty}')
print('Seiten mit "Vocabulary" im Text:', hits[:40])

# Probe: erste Textseite komplett
for i in range(len(doc)):
    t = doc[i].get_text().strip()
    if t:
        print(f'\n== Erste Textseite: 0-based {i} ==')
        print(t[:400])
        break

# eine Vocabulary-Seite als dict probe
if hits:
    p = hits[0]
    d = doc[p].get_text('dict')
    nspans = sum(len(l['spans']) for b in d['blocks'] for l in b.get('lines', []))
    print(f'\n== 0-based Seite {p}: {nspans} spans ==')
    spans = []
    for block in d['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                txt = span['text'].strip()
                if txt:
                    spans.append((span['bbox'][1], span['bbox'][0], span['font'], span['text']))
    spans.sort(key=lambda s: (s[0], s[1]))
    for y, x, font, txt in spans[:25]:
        print(f'y={y:6.0f} x={x:6.0f} {font[:24]:24} | {txt[:60]}')