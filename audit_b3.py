#!/usr/bin/env python3
"""Semantic audit Band 3 (Access 3) — manuell gegen OCR-Quelle geprueft.

Format identisch zu audit_b1.json: [{band, unit, en, de, flags[]}].
Jedes Paar aus seed/access3.json (374) gegen die Quelle import/Access_G9_Band3_text.txt
geprueft (Kontextzeilen je Verdachtsfall ausgezogen; Wurzelklassen wie Audit 2.0).
"""
import json
import io
from collections import OrderedDict

SEED = 'seed/access3.json'
OUT = 'audit_b3.json'
BAND = 3

# (unit, en) -> flags  (jeder Kandidat gegen Quelle verifiziert, nicht geraten)
FLAGS = OrderedDict()
FLAGS[(1, 'Christmas')] = ['beispiel-anhang']           # 'Weihnachten Der 1. Weihnachtsfeiertag heisst Christmas Day .' -> trailing-Info-Satz
FLAGS[(1, 'eastbound')] = ['rest-nachbareintrag']       # 'Richtung Osten northbound' -> Kopfwoerter der Nachbarn haengen dran
FLAGS[(1, 'westbound')] = ['invertiert', 'rest-nachbareintrag']  # de='eastbound' (EN des Nachbarn); richtig: 'Richtung Westen'
FLAGS[(1, 'held, held')] = ['en-falsch', 'abgeschnitten']  # en=Past-Formen; Kopf '(to) hold onto sth.' fehlt, de-Folgezeile abgeschnitten
FLAGS[(1, 'whistle')] = ['beispiel-anhang']             # '(Triller-)Pfeife a' -> Beispiel-Anfang 'a [whistle]' haengt in de
FLAGS[(1, 'pfeifen')] = ['invertiert', 'en-falsch']     # en ist de-Tail; Original '(to) blow a whistle'
FLAGS[(1, 'bent')] = ['invertiert', 'en-falsch', 'abgeschnitten']  # en=Past-Form; Kopf '(to) bend down'; Beispielsatz zerschnitten
FLAGS[(2, 'empty')] = ['antonym-tail']                  # 'leer full'
FLAGS[(2, 'Die')] = ['invertiert', 'eintrag-verlust']   # ORPHAN aus furniture-Note ('Die Moebel sind neu.'); furniture-Entry selbst fehlt
FLAGS[(2, 'Tafel')] = ['invertiert', 'eintrag-verlust'] # de-Rest des bar-Entry ('Schokolade'); bar selbst korrekt vorhanden
FLAGS[(2, '(to) delete')] = ['beispiel-anhang']         # '... streichen (to) delete a paragraph/a program'
FLAGS[(2, '(to) hate')] = ['antonym-tail']              # 'hassen (to) love'
FLAGS[(2, 'Mum says I')] = ['satzfragment']             # zerschnittener Beispielsatz zu '(to) spend time/money (on)'
FLAGS[(2, 'noisy')] = ['antonym-tail']                  # 'laut, laermend, voller Laerm quiet'
FLAGS[(2, 'sich hinlegen')] = ['invertiert', 'satzfragment']  # en=de; Original '(to) lie down'; de=Beispiel-Anfang "I'm so tired."
FLAGS[(2, 'such a + noun')] = ['grammatik-meta']        # Grammatik-Regelzeile 'so + adjective' als Vokabelpaar
FLAGS[(2, '… so')] = ['grammatik-meta', 'satzfragment'] # Regelsplit '… so ein netter Mensch … so nett'
FLAGS[(3, 'factual')] = ['abgeschnitten']               # '... faktisch; den' (Tatsachen entsprechend fehlt)
FLAGS[(3, 'led')] = ['en-falsch', 'eintrag-verlust']    # Past-Form als Kopf; '(to) lead' (fuehren, leiten) fehlt
FLAGS[(3, 'like')] = ['invertiert', 'en-falsch']        # en sollte '(to) be/look like' sein, de 'aehneln'; 'als ob' verlorenes Reststueck
FLAGS[(3, '(to) order')] = ['rest-nachbareintrag']      # 'ordnen (to) order' -> EN-Kopf-Wiederholung im de-Feld
FLAGS[(3, 'solved')] = ['satzfragment']                 # 'them all.' aus zerschnittenem Beispielsatz
FLAGS[(4, '(to) build')] = ['invertiert', 'en-falsch']  # de-Feld haelt Past-Forms 'built, built'; 'bauen' fehlt
FLAGS[(4, 'Nachteil')] = ['fremdsprachen-rest']         # 'F le desavantage' -> frz. Woerterbuchrest statt 'disadvantage'
FLAGS[(4, '(to) unpack')] = ['antonym-tail']            # 'auspacken (to) pack (packen, einpacken)'
FLAGS[(4, 'From the plane, the buildings')] = ['satzfragment']  # Beispielsatz-Halbteil als en/de
FLAGS[(5, 'sheepdog')] = ['satzfragment', 'abgeschnitten']  # Note 'Mit sheepdog ... Border' mitten abgeschnitten
FLAGS[(5, '(to) control')] = ['abgeschnitten']          # '... beherrschen; unter' (unter Kontrolle halten/bekommen fehlt)
FLAGS[(5, 'burst, burst')] = ['en-falsch', 'beispiel-anhang']  # Kopf '(to) burst into tears' als Past-Forms; Beispielschnipsel im de


def main():
    seed = json.load(io.open(SEED, encoding='utf-8'))
    entries = []
    total = 0
    miss = []
    for ch in seed['chapters']:
        for en, de in ch['words']:
            total += 1
            fl = FLAGS.get((ch['num'], en))
            if fl:
                entries.append({'band': BAND, 'unit': ch['num'], 'en': en, 'de': de, 'flags': fl})
                del FLAGS[(ch['num'], en)]
    # leftover keys = Schluesse ohne Match im Seed (Tippfehler in der Tabelle)
    miss = list(FLAGS.keys())

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
        f.write('\n')

    rate = round(100.0 * len(entries) / total, 2)
    print(f'Geprüft: {total} Paare (Band {BAND}, {len(seed["chapters"])} Units)')
    print(f'Flaggs: {len(entries)}')
    print(f'Fehlrate: {rate}%')
    if miss:
        print('NICHT GEONDEM (' + len(miss).__str__() + '): ' + ', '.join(f'{u}|{e}' for u, e in miss))
    # je Unit
    per = {}
    for e in entries:
        per[e['unit']] = per.get(e['unit'], 0) + 1
    print('Je Unit: ' + ', '.join(f'U{u}={n}' for u, n in sorted(per.items())) + f' -> {OUT}')


if __name__ == '__main__':
    main()