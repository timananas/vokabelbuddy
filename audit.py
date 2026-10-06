#!/usr/bin/env python3
"""Strenge Audito-Stufe 1: programmatische Checks über alle Bände.
Schreibt audit_findings.json (Befunde) für Stufe 2 (semantische Subagenten-Prüfung)."""
import json
import re
import sys

ASCII_OK = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')


def en_suspicious(en):
    flags = []
    # Umlaut/ß im Englischen = invertierte Zeile
    if any(c in en for c in 'äöüßÄÖÜ'):
        flags.append('umlaut-im-en')
    if not all(c in ASCII_OK or c in ' ()\'’.,-;:/&…!?+' for c in en):
        flags.append('fremdzeichen-im-en')
    # Header-/Layout-Leichen
    if re.match(r'^(?:STEP |Part [AB]|Units? \d|Unit \d|Revision\b|Grammar\b|Practice\b|Skills\b|Dictionary\b|Irregular\b|Content\b|Writing\b|Reading\b|Listening\b|Speaking\b|Wordbank\b|Contents\b|Answer|Workbook\b|Test\b|Vocabulary\b|Review\b|Get ready|Let’s |Now |Look |Read |Listen |Write |Talk |Check |Ask |Choose |Complete |Find |Match |Saying |Numbers|Months|Time)', en):
        flags.append('header-oder-instr')
    if re.fullmatch(r'(?:\d+|[a-d])\.', en.strip()) or re.fullmatch(r'[YNU]\b.*', en):
        flags.append('liste-kurz')
    if len(en.split()) > 8:
        flags.append('zu-lang-en')
    if en.casefold() in ('english:', 'german:', 'f', 'l', 'ae', 'be', 'e.g.', 'i.e.', 'pl', '(pl)', '(no pl)', 'sb.', 'sth.', 'to'):
        if en.casefold() != 'to' or not en.startswith('(to)'):
            flags.append('marker-als-en')
    if en.count('…') > 1 or en.count('•') or en.count('[', 0, None) - en.count(']'):
        flags.append('klammer-rest')
    if re.search(r'\[\w+.*\]', en):
        flags.append('ipa-im-en')
    return flags


def de_suspicious(de):
    flags = []
    if not de.strip():
        return ['leer']
    if re.search(r'\[\S+[^\]]*\]', de):
        flags.append('ipa-im-de')
    # kein deutsches Zeichen + keine typischen de-Wörter → vermutlich englisches Fragment
    uml = any(c in de for c in 'äöüßÄÖÜ')
    if not uml and len(de.split()) >= 4:
        # typische legitime Fälle: Latin/F-Linien weg (bereits trimmed), Kürzel
        if not re.search(r'\b(der|die|das|ein|eine|ich|jn|jm|usw|pl|AE|BE|bes)\b', de, re.I):
            flags.append('de-ohne-deutsch')
    if re.search(r'[.!?]\s+[A-Z]?[a-z]+[.!?]\s*$', de) and not any(c in de for c in 'äöüßÄÖÜ'):
        flags.append('satz-rest')
    if re.search(r'Betonung:|stress:|adj:|noun:|verb:|adv:|opp:|syn:|(English|German):', de):
        flags.append('label-rest')
    if re.search(r'\b\d{4,}\b', de):
        flags.append('jahr-rest')
    if '£' in de or '$' in de:
        flags.append('preis-rest')
    if len(de.split()) > 10:
        flags.append('zu-lang-de')
    if re.search(r'(?:^|\s)[A-ZÄÖÜ]{1}[a-zäöüß]{2,}(?:oder|als|und|in|auf|zu)\b', de) and 'oder' in de:
        flags.append('smashed')
    return flags


def main():
    findings = []
    stats = {}
    for b in range(1, 7):
        d = json.load(open(f'seed/access{b}.json'))
        n_entries = sum(len(c['words']) for c in d['chapters'])
        n_flags = 0
        for c in d['chapters']:
            seen = {}
            for en, de in c['words']:
                flags = en_suspicious(en) + de_suspicious(de)
                k = en.casefold()
                if k in seen and seen[k].casefold() != de.casefold():
                    flags.append(f'dublette-diff-de:{seen[k][:30]}')
                seen[k] = de
                if flags:
                    n_flags += 1
                    findings.append({'band': b, 'unit': c['num'], 'en': en, 'de': de, 'flags': flags})
        stats[b] = {'entries': n_entries, 'flags': n_flags}
    json.dump(findings, open('audit_findings.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(stats, ensure_ascii=False))
    print(f'GESAMT: {sum(s["entries"] for s in stats.values())} Entries, {sum(s["flags"] for s in stats.values())} Flags → audit_findings.json')


if __name__ == '__main__':
    main()