#!/usr/bin/env python3
"""Green Line G9 (2019) — Vokabel-Parser v2.
Quelle: Klett-Vokabellisten (PDF-Tabellen, 4/5-Spalten).
STRUKTUR (per PDF-Analyse):
  Jeder Entry beginnt mit einem PAAR Markern:
    U-MARK (U1..U6|PUA|CI|GRS|AC1..4|SK1|SK2|ST1|ST2|UT1|UT2|FP1|FP2|TR1..3) = Abschnitt/Kapitel
    optional S-MARK (S1..S9) = Schulbuch-Seite innerhalb des Abschnitts -> ÜBERSPRINGEN
    (Band 1/2 haben oft S-Mark VOR jedem Eintrag: 'U1 / S1 / sister / IPA / Schwester / ukr')
  Danach: en, [ipa ('!' oder '[' beginnend, optional)], de(mehrzeilig), [ukr(mehrzeilig)]
  DE trimmt kyrillische Reste am Ende (is_ukr erkennt gemischte Zeilen nicht zuverlässig).
Ausgabe: seed/greenline1..6.json ({book, chapters:[{num, title, words:[[en,de],...]}]}).
Kapitel-Titel = lesbare Abschnittsnamen (OHNE Seitenzahlen — Tim will Seiten nicht sehen).
"""
import pymupdf, re, json, os

MARK_RE = re.compile(r'^(PUA|CI|GRS|U\d{1,2}|AC\d|TR\d)$')          # Kapitel-Marker
CELL_RE = re.compile(r'^(S\d{1,2}|SK\d?|ST\d?|UT\d?|FP\d?|CO\d?|GRS|R\d|W\d|T\d)$')  # ZellenMarker
PAGE_RE = re.compile(r'^S\d{1,2}$')
UKR_LO, UKR_HI = '\u0400', '\u04FF'

def is_ukr(line):
    t = line.strip()
    if not t:
        return False
    cyr = sum(1 for c in t if UKR_LO <= c <= UKR_HI)
    return cyr >= max(2, len(t.replace(' ', '')) * 0.4)

def lekt_name(m):
    mm = re.fullmatch(r'U(\d)', m)
    if mm: return f'Unit {mm.group(1)}'
    mm = re.fullmatch(r'AC(\d)', m)
    if mm: return f'Across cultures {mm.group(1)}'
    mm = re.fullmatch(r'TR(\d)', m)
    if mm: return f'Text review {mm.group(1)}'
    mm = re.fullmatch(r'(SK|ST|UT|FP)(\d)', m)
    if mm:
        names = {'SK': 'Skills', 'ST': 'Study skills', 'UT': 'Unit task', 'FP': 'Final probe'}
        return f'{names[mm.group(1)]} {mm.group(2)}'
    if m == 'PUA': return 'Pre-Unit'
    if m == 'CI': return 'Check-in'
    if m == 'GRS': return 'Grammar/Skills'
    return m

def parse_band(band):
    d = pymupdf.open(f'/tmp/greenline/g9_vokabelliste_{band}.pdf')
    entries = []          # dicts {lekt, page, en, de_lines}
    cur = None
    state = 'idle'        # idle|wait_en|wait_ipa_de|de|ukr
    for pg in d:
        for raw in pg.get_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith('Vokabular zu Green Line'):
                continue
            if line in ('Lektion', 'Englisch', 'Phonetik', 'Deutsch', 'Ukrainisch'):
                continue
            if CELL_RE.match(line):
                # Zellen-Marker (Seite/Skills/…) — die Vokabel folgt im selben Kapitel:
                if cur is not None:
                    cur['page'] = line
                state = 'wait_en'
                continue
            if MARK_RE.match(line):
                if cur and cur['en']:
                    entries.append(cur)
                cur = {'lekt': line, 'page': '', 'en': '', 'de_lines': []}
                state = 'wait_en'
                continue
            if cur is None:
                continue
            if state == 'wait_en':
                # PDF-Zellen-Merge: 'en !ipa de' auf EINER Zeile — NUR wenn NACH dem '!' mehr
                # als nur ein Satzzeichen folgt (ein '!' am Ende = Ausrufezeichen des Satzes!):
                if '!' in line.rstrip()[:-1]:   # nicht das letze Zeichen!
                    head, tail = line.split('!', 1)
                    cur['en'] = head.strip()
                    qpos = tail.rfind('?')
                    if qpos >= 0:
                        der = tail[qpos+1:].strip(' ,;-')
                        if der:
                            cur['de_lines'].append(der)
                    state = 'de'
                    continue
                cur['en'] = line
                state = 'wait_ipa_de'
                continue
            if state == 'wait_ipa_de':
                if line.startswith('!') or line.startswith('['):
                    state = 'de'     # IPA überspringen
                    continue
                state = 'de'
            if state == 'de':
                if is_ukr(line):
                    state = 'ukr'
                    continue
                # Verspätete IPA-Zeile (langes EN über 2 PDF-Zeilen) — an '!+…? '-grenze split:
                if line.startswith('!') or line.startswith('['):
                    qpos = line.rfind('?')
                    if qpos >= 0:
                        der = line[qpos+1:].strip(' ,;-')
                        if der:
                            cur['de_lines'].append(der)
                        state = 'ukr'  # danach kommt ggf. die ukr-Zeile
                        continue
                    # kein '?'-Ende → reine IPA-Zeile (selten) — überspringen:
                    continue
                cur['de_lines'].append(line)
                continue
            # ukrainische Fortsetzung ignorieren
    if cur and cur['en']:
        entries.append(cur)
    # kyrillische Reste in DE trimmen:
    out = []
    for e in entries:
        de = ' '.join(e['de_lines'])
        i = next((j for j, c in enumerate(de) if UKR_LO <= c <= UKR_HI), len(de))
        if i < len(de):
            de = de[:i]
        de = de.strip(' ;,')
        if de:
            out.append({'lekt': e['lekt'], 'page': e['page'], 'en': e['en'], 'de': de})
    return out

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, 'seed')
    total = 0
    for band in range(1, 7):
        entries = parse_band(band)
        order = []
        for e in entries:
            if e['lekt'] not in order:
                order.append(e['lekt'])
        chapters = []
        for lekt in order:
            words = []
            for e in entries:
                if e['lekt'] == lekt and e['de']:
                    words.append([e['en'].strip(), e['de'].strip()])
            if words:
                chapters.append({'num': len(chapters) + 1,
                                 'title': lekt_name(lekt),
                                 'words': words})
        n = sum(len(c['words']) for c in chapters)
        total += n
        with open(f'{out_dir}/greenline{band}.json', 'w') as f:
            json.dump({'book': f'greenline{band}', 'chapters': chapters}, f, ensure_ascii=False, indent=1)
        tops = [f"{c['title']} ({len(c['words'])})" for c in chapters[:5]]
        print(f'Band {band}: {n} Vokabeln in {len(chapters)} Kapiteln →', ' · '.join(tops), '…')
    print('SUMME:', total)

if __name__ == '__main__':
    main()