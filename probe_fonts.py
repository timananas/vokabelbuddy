#!/usr/bin/env python3
"""Probe: Font-/Formatstruktur einer Vocabulary-Seite verstehen."""
import sys
import pymupdf

path = sys.argv[1] if len(sys.argv) > 1 else 'import/Access_G9_Band1_5Schuljahr.pdf'
page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 190  # 0-based -> PDF-Seite 191

doc = pymupdf.open(path)
page = doc[page_no]
print(f'PDF-Seite {page_no+1}, Größe: {page.rect}')

d = page.get_text('dict')
spans = []
for block in d['blocks']:
    for line in block.get('lines', []):
        for span in line['spans']:
            txt = span['text'].strip()
            if not txt:
                continue
            spans.append({
                'text': txt[:60],
                'font': span['font'],
                'size': round(span['size'], 1),
                'flags': span['flags'],
                'x': round(span['bbox'][0], 0),
                'y': round(span['bbox'][1], 0),
            })

# Font-Inventar
from collections import Counter
inv = Counter((s['font'], s['size'], s['flags'] & 16) for s in spans)
print('\n== Font-Inventar (font, size, bold-flag) ==')
for (font, size, bold), n in inv.most_common(30):
    print(f'{n:4}x  {font:32} size={size:5} bold={bold}')

print('\n== Erste 45 Spans (Reihenfolge) ==')
for s in spans[:45]:
    bold = 'B' if s['flags'] & 16 else ' '
    print(f"y={s['y']:6.0f} x={s['x']:6.0f} {bold} {s['font'][:26]:26} {s['size']:5} | {s['text']}")

# Deutsch-Erkennung: Spans mit Umlauten
print('\n== Beispiel-Spans mit Umlauten (vermutl. Übersetzungen) ==')
n = 0
for s in spans:
    if n >= 12:
        break
    if any(c in s['text'] for c in 'äöüßÄÖÜ'):
        bold = 'B' if s['flags'] & 16 else ' '
        print(f"y={s['y']:6.0f} x={s['x']:6.0f} {bold} {s['font'][:26]:26} | {s['text']}")
        n += 1