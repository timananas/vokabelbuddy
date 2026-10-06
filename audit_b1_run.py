# -*- coding: utf-8 -*-
"""Voll-Audit aller 501 seed/access1.json-Paare -> audit_b1.json (nur Flaggs)."""
import json, sys

SEED = '/home/hermesadmin/workspace/vokabelbuddy/seed/access1.json'
OUT  = '/home/hermesadmin/workspace/vokabelbuddy/audit_b1.json'

data = json.load(open(SEED, encoding='utf8'))

# Index: (chapter, en) -> (pos 1-basiert, de)  [en je Kapitel eindeutig]
entries = {}
per_ch = {}
for ch in data['chapters']:
    per_ch[ch['num']] = len(ch['words'])
    for i, (en, de) in enumerate(ch['words'], 1):
        key = (ch['num'], en)
        if key in entries:
            print('DUP-KEY:', key); sys.exit(1)
        entries[key] = (i, de)

total = sum(per_ch.values())

FLAGS = []  # (chapter, en, issue, fix)
def f(ch, en, issue, fix):
    FLAGS.append(dict(chapter=ch, en=en, issue=issue, fix=fix))

# ---------------- Kapitel 1 ----------------
f(1, "she",
  "Spaltenbruch: de ist EN-Rest '– her name', keine echte Übersetzung (she = sie; 'her name = ihr Name' wäre eigenes Paar)",
  "she = sie")
f(1, "You\u2019re late.",
  "angehängter EN-Satz 'Wait for me, please.' im de-Feld (OCR-Spaltenbruch)",
  "You\u2019re late. = Du bist spät dran./Du bist zu spät.")
f(1, "Let\u2019s go home .",
  "halbes Paar: en ist ganzer Satz, de nur Infinitiv 'nach Hause gehen'; zudem Leerzeichen vor Punkt",
  "Let\u2019s go home. = Lass uns nach Hause gehen./Gehen wir nach Hause!")
f(1, "Bye.",
  "EN-Rest 'Hello.' im de-Feld (Antonym-/Spaltenartefakt)",
  "Bye. = Tschüs.")
f(1, "short",
  "angehängtes EN-Antonym 'long' im de-Feld (Antonym-Icon-Artefakt)",
  "short = kurz")
f(1, "telephone",
  "de ist EN-Wort ('phone'), Übersetzung fehlt komplett",
  "telephone = Telefon")
f(1, "(to) answer",
  "angehängter EN-Rest '(to) ask' im de-Feld (OCR-Spaltenbruch)",
  "(to) answer = antworten; beantworten")
f(1, "quarter past",
  "zerbrochene Uhren-Phrase: de ist EN-Rest ('ten'); korrekt wäre 'quarter past ten = Viertel nach zehn'",
  "quarter past ten = Viertel nach zehn")
f(1, "half past",
  "zerbrochene Uhren-Phrase: de ist EN-Rest ('ten'); korrekt wäre 'half past ten = halb elf'",
  "half past ten = halb elf")
f(1, "quarter to",
  "zerbrochene Uhren-Phrase: de ist EN-Rest ('eleven'); korrekt wäre 'quarter to eleven = Viertel vor elf'",
  "quarter to eleven = Viertel vor elf")
f(1, "ICT 1",
  "OCR-Bruch: Ziffer '1' an en geklebt, de = verschmiertes 'Informationsund' (Zeilenumbruch nach 'Informations-'); Übersetzung unvollständig",
  "ICT = Informations- und Kommunikationstechnologie")
f(1, "3 March",
  "unvollständiger Datumseintrag: de 'März' ohne den Tag",
  "3 March = der 3. März")
f(1, "this",
  "Spaltenbruch: EN-Wörter ('place/break/subject') mit deutschen Entsprechungen im de-Feld vermengt (eigentlich 3 Paare)",
  "this = dieser/diese/dieses")
f(1, "corner shop",
  "ungenau: ein corner shop ist der kleine Laden an der Ecke (Tante-Emma-Laden), nicht irgendein Laden",
  "corner shop = Tante-Emma-Laden")

# ---------------- Kapitel 2 ----------------
f(2, "small",
  "angehängtes EN-Antonym 'big' im de-Feld (Antonym-Icon-Artefakt)",
  "small = klein")
f(2, "(to) have breakfast",
  "angehängter EN-Rest 'Silky\u2019s breakfast' im de-Feld (OCR)",
  "(to) have breakfast = frühstücken")
f(2, "(to) text a friend",
  "abgebrochene Übersetzung (Objekt/Verb fehlen: '… eine Nachricht (schreiben)')",
  "(to) text a friend = jm. eine Nachricht schreiben")
f(2, "paper",
  "Bedeutung verdreht: paper = Papier; 'Zeitung' gilt nur für '(the) paper(s)' – Kap. 4 hat korrekt 'paper = Papier'",
  "paper = Papier")
f(2, "at night",
  "angehängte EN-Kreuzverweis-Reste 'in the morning/afternoon/evening' im de-Feld",
  "at night = nachts, in der Nacht")
f(2, "on the floor . floor",
  "en OCR-Bruch ('. floor' doppelt/an klebt); de müsste die Präposition mit abbilden",
  "on the floor = auf dem (Fuß-)Boden")
f(2, "trophy",
  "angehängter EN-Plural-Rest 'trophies' im de-Feld",
  "trophy = Pokal; Trophäe")
f(2, "hot cold cold",
  "en-Feld mit verdoppelten Antonym-Artefakten; gemeint ist 'cold' ('hot = heiß' steht korrekt daneben)",
  "cold = kalt")
f(2, "cream",
  "de ist EN-Wort (invertiert aus 'cream / jam'-Spaltenbruch); 'jam = Marmelade' steht korrekt daneben, cream = Sahne/Rahm",
  "cream = Sahne, Rahm")
f(2, "Can you",
  "Satz zweigeteilt, de ist EN-Wort; kein Wortpaar ('(to) jump = springen' existiert bereits)",
  "DELETE")

# ---------------- Kapitel 3 ----------------
f(3, "story",
  "angehängter EN-Rest 'one story – two, three, four stories' (Irregular-plural-Vermerk) im de-Feld",
  "story = Geschichte, Erzählung")
f(3, "Ihre gesamte",
  "deutscher Satzfragment als en (Spaltenbruch aus Grammatikkasten zu 'all'); kein Wortpaar",
  "DELETE")
f(3, "Meine",
  "Satzfragment-Bruch ('Jeans ist blau.' gehört zu 'Meine Jeans ist blau.'); kein Wortpaar",
  "DELETE")
f(3, "(nie",
  "Fragment der Häufigkeitsskala '(never – sometimes – always)' aus dem Grammatikkasten; kein Wortpaar",
  "DELETE")
f(3, "(to) have to go/work/\u2026",
  "angehängter EN-Satz 'Look, it\u2019s late.' im de-Feld (OCR)",
  "(to) have to go/work/\u2026 = gehen/arbeiten/\u2026 müssen")
f(3, "Listen to the song.",
  "abgebrochene Übersetzung (Objekt fehlt: '… das Lied an.')",
  "Listen to the song. = Hör dir/Hört euch das Lied an.")

# ---------------- Kapitel 4 ----------------
f(4, "coin",
  "angehängter EN-Rest 'some coins' im de-Feld (Spaltenbruch)",
  "coin = Münze")
f(4, "Mobiltelefon, Handy \u201eHandy\u201c",
  "invertiert/zerbrochen: deutscher false-friend-Kastentext im en-Feld ('Handy' klingt zwar englisch…), Fortsetzung im de; eigentlich fehlender Eintrag 'mobile phone'",
  "mobile phone = Mobiltelefon, Handy")
f(4, "(to) buy",
  "angehängter EN-Rest '(to) buy' im de-Feld (OCR)",
  "(to) buy = kaufen")
f(4, "someone",
  "zerbrochene Phrase 'Someone is wrong = Jemand irrt sich'; de enthält EN-Rest 'is wrong'; Grundpaar: someone = jemand",
  "someone = jemand")
f(4, "What\u2019s",
  "Satz zweigeteilt (Spaltenbruch): de beginnt mit EN-Rest 'your poster about?'",
  "What\u2019s your poster about? = Wovon handelt dein Poster? Worum geht es bei deinem Poster?")
f(4, "after",
  "angehängtes EN-Antonym 'before' im de-Feld",
  "after = nachdem")
f(4, "niemand",
  "invertiert: deutsches Wort im en-Feld, EN-Wort als de; everyone = alle, jeder (niemand = nobody/no one)",
  "everyone = alle, jeder")

# ---------------- Build output ----------------
out = []
for fl in FLAGS:
    key = (fl['chapter'], fl['en'])
    if key not in entries:
        print('MISS:', key); sys.exit(1)
    pos, de = entries[key]
    out.append({
        "type": "B1",
        "chapter": fl['chapter'],
        "pos": pos,
        "en": fl['en'],
        "de": de,
        "issue": fl['issue'],
        "fix": fl['fix'],
    })

with open(OUT, 'w', encoding='utf8') as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
    fh.write('\n')

# ---------------- Verification report ----------------
by_ch = {}
for o in out:
    by_ch[o['chapter']] = by_ch.get(o['chapter'], 0) + 1
for o in out:
    print(f"K{o['chapter']} pos {o['chapter']}-{o['pos']:3d}: {o['en']!r} = {o['de']!r}  ->  {o['fix']}")
print()
print('KAPITEL:', {k: v for k, v in per_ch.items()})

err = round(100.0 * len(out) / total, 1)
print(f'GEprüfte Paare gesamt: {total}  (K1 {per_ch[1]} / K2 {per_ch[2]} / K3 {per_ch[3]} / K4 {per_ch[4]})')
print(f'FLAGGS: {len(out)}  (K1 {by_ch.get(1,0)} / K2 {by_ch.get(2,0)} / K3 {by_ch.get(3,0)} / K4 {by_ch.get(4,0)})')
print(f'GESCHÄTZTE FEHLRATE: {err} % ({len(out)}/{total})')