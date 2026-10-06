#!/usr/bin/env python3
"""Finalize: letzte Säuberung der seed-JSONs (Post-Prozessor über die geparsten Daten).
Entfernt invertierte Zeilen, IPA/Bracket-Leichen, Ziffern-Entries, Label-Reste —
behält legitime umlautlose deutsche Übersetzungen (Morphologie-Check)."""
import json
import re

IPA_CHARS = set('ɪʊʌɑɒɔæəθðʃʒŋɡˈˌːˑæʊɒəɜɐɛɪɔʉʌʏçɬŋʃʒθðʔ˞̩̥')

DE_STOP = re.compile(
    r'(^|[\s,.:!?/()\[\]])(?:der|die|das|den|dem|des|ein|eine|einen|einem|einer|eines|'
    r'ich|du|er|sie|es|wir|ihr|ihre|ihren|ihrer|ihrer|ihr|ihrer|ihm|ihnen|'
    r'mein|meine|meinen|meiner|dein|deine|deinen|unser|unsere|'
    r'und|oder|aber|mit|von|zum|zur|zu|für|auf|aus|bei|nach|in|im|an|am|'
    r'jn|jm|etwas|sich|nicht|auch|wieder|sind|ist|war|habe|hast|hat|haben|'
    r'werde|wird|werden|kann|kannst|könnte|muss|müssen|soll|sollte|will|willst|'
    r'möchte|gute|guten|morgen|abend|nacht|danke|dank|bitte|ja|nein|hallo|'
    r'tschüs|tschüss|viel|mehr|man|manchmal|nur|noch|schon|immer|alle|alles|'
    r'zu Hause|daheim|hier|dort|heute|morgen|gestern|jetzt|dann|wenn|weil|'
    r'wie|so|sehr|etwa|ca|usw)(?=$|[\s,.:!?/()\[\]])')

MORPH = re.compile(r'(ung|heit|keit|schaft|lich|isch|bar|chen|tum)($|s\b)')

EN_STOPISH = re.compile(
    r'(^|[\s,.:!?/()\[\]])(?:the|and|please|wait|you|your|yours|for|with|when|what|'
    r'how|can|could|she|he|they|we|it|this|that|there|here|have|has|are|was|were|is|'
    r'think|like|see|look|listen|say|sorry|hello|bye|thanks|welcome|well|good|nice|'
    r'club|not|don|didn|join|our|from|about|one|two|three|four|five|six)(?=$|[\s,.:!?/()\[\]])')


def has_ipa(text):
    """Klammern mit IPA-Inhalt (oder reine IPA-Brackets)."""
    for m in re.finditer(r'\[([^\]]*)\]', text):
        content = m.group(1)
        if content.strip() and any(c in IPA_CHARS for c in content):
            return True
    # auch ohne bracket: IPA-Zeichen im Fließtext
    if any(c in IPA_CHARS for c in text.replace('[', '').replace(']', '')):
        return True
    return False


def de_german(de):
    """Deutsches Kennzeichen: Umlaut/ß, de-Stopwort, typische Endung, ge-…-t/-st Partizip."""
    if any(c in de for c in 'äöüßÄÖÜ'):
        return True
    if DE_STOP.search(de.lower()):
        return True
    words = re.findall(r'[a-zäöüß]+', de.lower())
    for w in words:
        if MORPH.search(w):
            return True
        if w.startswith('ge') and w.endswith(('t', 'st', 'en')) and len(w) > 5:
            return True
        if w.endswith(('en', 'er', 'em', 'es', 'nd', 'st', 'te', 'rn')) and len(w) > 4:
            # Einzel-Kandidat reicht nicht (teacher/water!) — zähle: ≥2 solche Wörter ODER 1 langes
            pass
    # ≥2 Wörter mit de-typischen Endungen ODER 1 Wort ≥9 Zeichen mit solcher Endung
    hits = [w for w in words if len(w) > 4 and w.endswith(('en', 'er', 'em', 'es', 'ung', 'ich', 'lich', 'bar'))]
    return len(hits) >= 2 or any(len(w) >= 9 and w.endswith(('en', 'er', 'ung', 'lich')) for w in words)


ANTonym_TAIL = re.compile(r'\s+\b(?:true|false|opp|opposite|syn|the opposite)\b\s*$', re.I)


def en_sentence_junk(en):
    return re.fullmatch(r'\d+', en.strip()) is not None


def finalize_entry(en, de):
    en, de = str(en or '').strip(), str(de or '').strip()
    # Grammatik-Label-Starts in de oder en ('verb: (to)', 'pl headscarves', '( AusE infml:')
    if re.match(r'^(?:pl\b|verb[:\s]|adj[:\s]|noun[:\s]|adv[:\s]|AusE|AmE|BrE|infml)', de) \
       or re.match(r'^(?:verb:|adj:|noun:|adv:|pl\b|AusE|AmE|BrE|infml)', en) \
       or re.match(r'^\(\s*(?:Aus|Am|Br)E', en) or re.match(r'^\(\s*infml', en):
        return None
    if re.match(r'^(?:pl\b|verb[:\s]|adj[:\s]|noun[:\s]|adv[:\s]|AusE|AmE|BrE|infml)', de):
        return None

    en = re.sub(r'\s+', ' ', en or '').strip()
    de = re.sub(r'\s+', ' ', de or '').strip()
    if not en or not de:
        return None
    # Lautschrift/Brackets jeglicher Form als Wortsinn → Leiche
    if has_ipa(en) or has_ipa(de):
        return None
    # reine Ziffern/Zahlwörter-OCR-Zeilen
    if en_sentence_junk(en) or re.match(r'^\d+\s+(hundred|and\b)', en) or re.fullmatch(r'\d+ [a-z-]+', en):
        return None
    if re.fullmatch(r'(?:\d+|\.|,)\s*', en) or de.strip() in ('…', '-', '(no pl)', 'pl', '(pl)'):
        return None
    # Label-Reste
    for lab in ('English:', 'German:', 'Betonung:', 'stress:', 'Aussprache:'):
        if lab in en or lab in de:
            return None
    # EN-Seite beginnt mit '=', '–', '•', 'F ', 'L ' → Layout-Leiche
    if re.match(r'^(?:[=–•—]|F\s|L\s)', en):
        return None
    # Invertierte/explanation-Zeilen: EN hat deutsch (Umlaut) und DE hat keins
    if any(c in en for c in 'äöüßÄÖÜ') and not de_german(de):
        return None
    # de enthält klaren englischen Satz und en ist kein Vokabel-Typ (Satzgrenzdinger)
    if len(de.split()) >= 4 and not de_german(de):
        return None
    # de mit führender Ziffer-Punkt-Liste ('1. abgeben' etc. ok) — bleibt
    # en rein aus 1–2 Buchstaben ohne Kontext
    if len(en.strip('(). –')) <= 2 and '…' not in en and len(en.strip()) < 4:
        return None
    # de mit führendem/bestandteil englischem Satzfragment ('them all.', 'I was th…')
    # EN-Satzzeichen + de überwiegend ASCII und enthält EN-Stopwort → Leiche
    if (re.search(r"[a-z]'?[a-z]*\s+[a-z]", de, re.I) and not de_german(de)
            and EN_STOPISH.search(de.lower())):
        return None
    # en mit Satzmitte-Bruch ('A recent discovery shows tha') — abgeschnittene Satzwörter
    if len(en.split()) >= 5 and en[0:1].isupper() and re.search(r'[a-z]\.$|[a-z] tha$', en.lower()):
        return None
    # de = 2-4 Wörter, beginnt KAPITAL und ist kein deutscher Satz → Beispielsatz-Leiche
    # NUR wenn de NICHT mit de-Stopwort/-Morphologie beginnt (Lineal/Bleistift sind LEGAL!)
    if (len(de.split()) in (2, 3, 4) and re.match(r'^[A-Z][a-z]+\.?', de)
            and not de_german(de) and len(en.split()) <= 3):
        return None
    # de mit kleingeschriebenem EN-Wort-Anhang nach legitimer Übersetzung:
    # 'courteous = höflich polite, showing respect' / 'extremely hot = äußerst heiß very confused sehr verwirrt'
    m = re.match(r'^(.*?[a-zäöüßA-Z](?:lich|eiß|oll|en|ur|it|os|ig|re)?)(\s+[a-z]+(?:,\s*[a-z]+.*)?)$', de) if de else None
    if m and not de_german(de[len(m.group(1)):]) and any(
            w.lower() in ('polite','showing','very','confused','quite','rather','somewhat','person',
                          'people','something','someone','used','describing','describes') for w in de.split()):
        de = m.group(1).rstrip(' ,')
    # 'particularly = or in particular' — de IST EN: Rein-EN-Übersetzung einer EN-Vokabel = kein de-Kern
    # → verwerfen, wenn WEDER Umlaut NOCH de-Stop NOCH Morphologie NOCH Cognate (gleiche Schreibung)
    if (not any(c in de for c in 'äöüßÄÖÜ') and not de_german(de)
            and en.rstrip('.').casefold() == de.rstrip('.').casefold()):
        return None
    # Muster: de IST ein englisches Einzel-/Kurz-Past-Form-Wort — legitime Lehrbuch-Info, aber als
    # MC-Vokabel unbrauchbar → verwerfen (Past-Form-Zeilen im Buch sind reine Verbtabellen)
    irregular_de = re.fullmatch(r"(?:shone|shot|went|came|made|said|saw|caught|chose|brought|thought|taught|fought|sold|told|sat|swam|sang|drank|ran|ate|fell|felt|found|gave|grew|held|heard|kept|knew|laid|led|left|lost|meant|met|paid|put|read|rode|rose|took|woke|wore|won|wrote|broke|built|burnt|bought|bid|bit|blew|drew|drove|fed|hid|hit)('|’)?s?", de.strip())
    if irregular_de or (de.strip().isalpha() and len(de.strip()) <= 7 and de.isascii() and de.strip() in ('shone','shot') ):
        return None
    # dt-Wort + Kapitalanfang + englischer Satz dahinter → den Satzteil abschneiden
    m2 = re.search(r'^(.*?\S)\s+([A-Z][a-z’\'-]*(?:\s+[a-z’\'-]+){1,8}[.!?]?)$', de) if de else None
    if m2 and EN_STOPISH.search(m2.group(2).lower()):
        de = m2.group(1).rstrip(' ,;')
    # Beide Seiten 'rein-englisch' (kein einziges de-Merkmal) und en ist EN-Stopword → Leiche
    if (not any(c in de for c in 'äöüßÄÖÜ') and not de_german(de)
            and EN_STOPISH.search(en.lower()) and EN_STOPISH.search(de.lower())):
        return None
    # EN mit Klammer-/Fragment-Kontrollleichen: '1) (to)', 'sth./sb. )', 'dealt, dealt'-Doppelformen ok
    if re.match(r'^\d\s*[\)\.]', en) or re.match(r'^\w+\./\w+\.? ?\)', en):
        return None
    # de beginnt mit kleingeschriebenem EN-Wort + en ist Vokabel: 'tall tall', 'whenever = wann wann'?
    # OCR-Reinigung
    if en.rstrip() == de.rstrip():
        return None
    # Doppel-Wort im EN ('tall tall') → OCR-Wiederholung → EN vereinen oder verwerfen
    ws = en.split()
    if len(ws) == 2 and ws[0].casefold() == ws[1].casefold():
        en = ws[0]
    # 'de' beginnt mit klein-satzteil ('paused', 'them all.') — nach de_german-Check ist das oben geprüft
    # EN-Fragments: '( or: somebody' / 'sth./sb. )' — beginnt mit '(' enthält ':'/')'-Reste
    if re.match(r'^\(\s*(?:or|also|pl| AE|BE|bes|infml)', en) or re.match(r'^\S*\)\s*$', en):
        return None
    # de mit angehängtem ANTONYM-Paar ('schwierig, schwer easy' / 'kämpfen fighter Kämpfer/in') —
    # klein-EN-Wort nach deutschem Teil: schneide ab letztem klein-EN-Wortblock (ohne de-Merkmale)
    m3 = re.search(r'^(.*?[a-zäöüß]{3,})\s+([a-z’\'-]{3,}(?:\s+[a-z’\'-]{2,})*)$', de) if de else None
    if (m3 and not de_german(m3.group(2))) and (EN_STOPISH.search(m3.group(2).lower()) or ANTonym_TAIL.search(' ' + m3.group(2))):
        de = m3.group(1)
    # EVIDENTE Junk-Klassen (Runde 3):
    # EN/DE endet mit Layout-Rest (Doppelpunkt, Gedankenstrich, offene Klammer, Bindestrich-Wort)
    if en.rstrip().endswith((':', '–', '—', '(', ',')) or de.rstrip().endswith((':', '–', '—', '(', ',')):
        return None
    if en.rstrip().endswith('-') and '…' not in en:
        return None  # 'multi-' Präfix-Fragmente
    # EN beginnt als DEUTSCHER Satz (Erklärungs-Fragmente 'Du schreibst: …', 'Mit needn't …')
    if (re.match(r'^(?:Mit |Der |Die |Das |Du |Deine|Dein|In den|In der|Was |Wie du?|Er |Es |Man |Am |Im |Zum |Zur |Vor |Bei |Auch )', en)
            and len(en.split()) >= 3 and not en.startswith('(to')):
        return None
    # EN einzelne Funktions-Fragment-Wörter ('What' als ganzer Prompt)
    if en in ('What', 'That', 'This', 'And', 'But', 'Or', 'If', 'So', 'There', 'Then', 'Where'):
        return None
    # DE rein-englischer Anhang (2+ Wörter, EN-Stopwort, kein deutsches Merkmal)
    if (len(de.split()) >= 2 and not de_german(de) and EN_STOPISH.search(de.lower())
            and EN_STOPISH.search(en.lower())):
        return None
    # Runde-4-Klassen (aus Live-Befund):
    # 'simple past:'/'irregular'-Grammatikzeilen (sind Verbtabellen, keine Vokabeln)
    if re.search(r'simple (past|present|perfect|progressive)|irregular|past participle', en, re.I):
        return None
    # EN mit '= 1.'-Aufzählungsrest / Gleichheitszeichen
    if re.search(r'\s=\s', en):
        return None
    # '1/2 = a/one'-Bruch-Leichen + En mit Ziffern-Fraktion
    if re.match(r'^\d+/\d+', en) or re.match(r'^\d+\s*(?:=|–|—)', en):
        return None
    # EN 'Let's/he's/What's'-Auxiliar-Fragmente: Verb-Auxiliar OHNE Vokabel-Kern + de ist EN-Fragment
    if re.match(r"^[A-Za-z]+’s$|^[A-Za-z]+ n’t$|^[A-Za-z]+n’t$", en) and not de_german(de):
        return None
    # '( oft auch kurz:'-Klammer-Fragmente
    if re.match(r'^\(\s*(?:oft|auch|kurz|bes|informal|bes\.|AE|BE|no pl|pl\b|usw)', en):
        return None
    # de-voll-englische Sätze ('watch', 'musical', 'smoking', 'sprayed', 'frustrated . ...', 'woke up')
    if not de_german(de) and len(de.split()) <= 4 and EN_STOPISH.search(de.lower()):
        return None
    # EN-Satzfragment + rein-englisches Einzel-Wort als de ('The walls were' = 'sprayed') → Leiche
    if (re.fullmatch(r"[a-zA-Z’'-]+", de.strip()) and not de_german(de)
            and re.search(r"\b(?:was|were|is|are|’s|n’t|n't|has|have|had|will|would|got)\b", en)
            and len(en.split()) >= 2 and not de.strip().casefold() == en.strip().casefold()):
        return None
    # EN-Satzanfang + de ohne de-Merkmal oder kleingeschrieben ('We walked' = 'around')
    if (re.match(r"^(?:We|She|He|They|It|Don’t|And|But|This|That|There|Jack|Maya|Sam|The)\b", en)
            and len(en.split())>=2 and en[0].isupper()
            and not en.rstrip().endswith(('?','!','…'))
            and (not de_german(de) or bool(re.match(r'^[a-z]', de)))):
        return None
    # Aux-Bruch: Ein-Wort-Pronomen-EN + kleingeschriebener EN-Satzrest als de
    if (en in ('They','She','He','We','I','It','You','And','But','Then','There')
            and de[:1].islower() and not de_german(de)):
        return None
    # Übersetzungsreste mit Trailing-Komma-Leichen
    de = de.rstrip(' ,;').strip()
    en = en.rstrip(' ,;').strip()
    if not en or not de:
        return None
    return en, de


def main():
    for b in range(1, 7):
        p = f'seed/access{b}.json'
        d = json.load(open(p))
        dropped, kept = 0, 0
        for ch in d['chapters']:
            w2 = []
            for en, de in ch['words']:
                r = finalize_entry(en, de)
                if r is None:
                    dropped += 1
                else:
                    w2.append(list(r))
                    kept += 1
            ch['words'] = w2
        json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)
        print(f'Band {b}: {kept} behalten, {dropped} verworfen')
    print('FINALIZE OK')


if __name__ == '__main__':
    main()