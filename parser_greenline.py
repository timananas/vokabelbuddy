#!/usr/bin/env python3
"""Green Line G9 (2019) — Vokabel-Parser.
Quelle: Klett-Vokabellisten (PDF, 4-Spalten-Tabelle: Lektion/Englisch/[Phonetik]/Deutsch/[Ukrainisch]).
Band 6 hat KEINE Phonetik-Spalte; die ukrainische Spalte ist optional-falls vorhanden.
Ausgabe: seed/greenline1..6.json ({book:'greenlineN', chapters:[{num, title, words:[[en,de],...]}]}).
"""
import pymupdf, re, json, os, sys

LEKT_RE = re.compile(r'^(PUA|CI|GRS|U\d{1,2}|AC\d)$')
UKR_CHARS = re.compile(r'[\u0400-\u04FF\u02BC\u2019\u02BE\u0591-\u05F4]')

def is_ukr(line):
    t = line.strip()
    if not t:
        return False
    # Ukrainisch = >40 % kyrillische Zeichen
    cyr = sum(1 for c in t if '\u0400' <= c <= '\u04FF')
    return cyr >= max(2, len(t.replace(' ', '')) * 0.4)

def parse_band(band):
    path = f'/tmp/greenline/g9_vokabelliste_{band}.pdf'
    d = pymupdf.open(path)
    entries = []  # (lekt, en, de)
    cur = None    # {'lekt','en','de_lines'}
    state = 'idle'
    has_ipa = True
    for pg in d:
        for raw in pg.get_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith('Vokabular zu Green Line'):
                continue
            if line in ('Lektion', 'Englisch', 'Phonetik', 'Deutsch', 'Ukrainisch', 'GRS'):
                if line == 'Phonetik':
                    has_ipa = has_ipa  # Kopf — nicht kritisch
                continue
            if LEKT_RE.match(line):
                # Flush:
                if cur and cur['en']:
                    entries.append((cur['lekt'], cur['en'], ' '.join(cur['de_lines'])))
                cur = {'lekt': line, 'en': '', 'de_lines': []}
                state = 'en'
                continue
            if cur is None:
                continue
            if state == 'en':
                cur['en'] = line
                state = 'ipa_or_de'
                continue
            if state == 'ipa_or_de':
                # IPA-Zeilen starten mit '!' oder '[':
                if line.startswith('!') or line.startswith('['):
                    state = 'de'   # IPA überspringen
                    continue
                # sonst ist diese Zeile die erste Deutsch-Zeile:
                state = 'de'
            # Deutsch-Aufbau, bis ukrainisch erscheint:
            if state == 'de':
                if is_ukr(line):
                    state = 'ukr'
                    continue
                cur['de_lines'].append(line)
                continue
            # nach dem ukr-Start: nichts mehr in de_lines — sauber
            # ukrainische Fortsetzung — ignorieren:
            continue
    if cur and cur['en']:
        entries.append((cur['lekt'], cur['en'], ' '.join(cur['de_lines'])))
    # Cyril-Rest in de trimmen (Mix-Zeilen wie 'und і...'):
    cleaned = []
    for lekt, en, de in entries:
        i = next((j for j, c in enumerate(de) if '\u0400' <= c <= '\u04FF'), len(de))
        if i < len(de):
            de = de[:i]
        cleaned.append((lekt, en, de.strip(' ;,')))
    return cleaned, has_ipa


LEKT_TITEL = {
    'PUA': 'Pre-Unit / People & you',
    'CI': 'Check-in',
    'GRS': 'Grammar & Skills',
}

def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seed')
    stats = {}
    for band in range(1, 7):
        entries, _ = parse_band(band)
        # Kapitel gruppieren (in Buchreihenfolge):
        chapters = []
        order = []
        for lekt, en, de in entries:
            if lekt not in order:
                order.append(lekt)
        for lekt in order:
            title = LEKT_TITEL.get(lekt, lekt.replace('U', 'Unit ').replace('AC', 'Across cultures ') )
            words = []
            for lekt2, en, de in entries:
                if lekt2 == lekt and de:
                    words.append([en.strip(), de.strip()])
            chapters.append({'num': len(chapters) + 1, 'title': f'{lekt} · {title}', 'words': words})
        stats[band] = sum(len(c['words']) for c in chapters)
        with open(f'{out_dir}/greenline{band}.json', 'w') as f:
            json.dump({'book': f'greenline{band}', 'chapters': chapters}, f, ensure_ascii=False, indent=1)
        print(f'Band {band}: {stats[band]} Vokabeln in {len(chapters)} Kapiteln')
    print('SUMME:', sum(stats.values()))

if __name__ == '__main__':
    main()