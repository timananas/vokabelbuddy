#!/usr/bin/env python3
"""Parser: Access G9 Vocabulary-OCR-Texte -> seed/accessN.json.

Quelle: Access_G9_BandN_text.txt (=== Seite N === Markern, 'Vocabulary'-Seiten).
Erkennt Unit-Abschnitte und Entries nach Muster 'english [IPA] deutsch'
sowie no-IPA-Einträge 'english   deutsch'. Ausgabe: chapters[{num,title,words}]
in Buch-Reihenfolge (words[pos0] = pos 1).
"""
import json
import re
import sys
from collections import Counter

BANDS = {1: 'access1', 2: 'access2', 3: 'access3', 4: 'access4', 5: 'access5', 6: 'access6'}

PAGE_RE = re.compile(r'^=== Seite (\d+) ===\s*$')
UNIT_RE = re.compile(r'^(?:Unit)\s*(\d+)\s*(.*)$')
HERE_RE = re.compile(r'^Here\s+we\s+go\.?\s*$')
IPA_RE = re.compile(r'^\s*(?P<en>.+?)\s*\[(?P<ipa>[^\]]+)\]\s*(?P<de>.+?)\s*$')
GAP_RE = re.compile(r'^(?P<en>\S.*?)\s{2,}(?P<de>\S.*)$')

# Zeilen, die sicher KEINE Entries sind
SKIP_EXACT = {'Vocabulary', 'Numbers , p. 237'}
SYMBOL_CHART = re.compile(r'^\[[a-zæœəɪʊʌɑɒɔθðʃʒː•].*[•]')
SPelled = re.compile(r'^one hundred|^two hundred')


def load_lines(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return [l.rstrip('\n').rstrip('\r') for l in f]


def vocab_page_ranges(lines):
    """Vocabulary-Seitenbereiche je Band aus der Intro-Notiz:
    'Das  Vocabulary  (S.183–212) enthält ...' — OCR macht Doppel-Leerzeichen."""
    text = '\n'.join(lines)
    ranges = []
    for m in re.finditer(r'Vocabulary\s+?\(S\.?\s*(\d+)\s*[-–]\s*(\d+)\)', text):
        a, b = int(m.group(1)), int(m.group(2))
        if 100 < a < 400 and b > a and (b - a) < 120:
            ranges.append((a, b))
    if not ranges:
        return None
    return max(set(ranges), key=ranges.count) if ranges else None


_NUM_WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
    'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
    'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90, 'hundred': 100,
}


def spelled_num(s):
    """'two hundred and nine' -> 209; sonst None."""
    parts = s.lower().replace('-', ' ').split()
    total, cur = 0, 0
    seen = False
    for p in parts:
        if p == 'and':
            continue
        if p not in _NUM_WORDS:
            return None
        seen = True
        v = _NUM_WORDS[p]
        if v == 100:
            cur = (cur or 1) * 100
        else:
            cur += v
        total = cur
        # 'hundred and thirty-five': cur akkumuliert; vereinfachte Logik reicht hier
    return total if seen and 100 <= total < 500 else (total if seen and total < 100 else None)


def printed_page(body_first):
    """Gedruckte Buchseitenzahl aus den ersten Zeilen (Ziffern oder ausgeschrieben)."""
    for h in body_first[:6]:
        hs = h.strip()
        if re.fullmatch(r'\d{1,3}', hs):
            return int(hs)
        if 'hundred' in hs and len(hs) < 45:
            try:
                return spelled_num(hs)
            except Exception:
                pass
    return None


def vocab_pages(lines):
    """Vocabulary-Seiten: [(marker, start, end)].
    Marker↔Buchseiten-Offset ist pro Band konstant (Scan): aus Seiten mit
    gedruckter Zahl ableiten, dann Seiten wählen, deren abgeleitete Buchseite
    im Intro-Bereich (rng) liegt."""
    pages = []  # (lineno, marker)
    for i, l in enumerate(lines):
        m = PAGE_RE.match(l.strip())
        if m:
            pages.append((i, int(m.group(1))))
    rng = vocab_page_ranges(lines)
    if not rng:
        return [], None
    lo, hi = rng

    cands = []
    for j, (i, num) in enumerate(pages):
        end = pages[j + 1][0] if j + 1 < len(pages) else len(lines)
        body = [x.strip() for x in lines[i + 1:end] if x.strip()]
        bp = printed_page(body)
        if bp is not None:
            cands.append((num, bp))

    from collections import Counter
    offs = Counter(m - p for m, p in cands if lo - 60 <= m <= hi + 60)
    if not offs:
        return [], rng
    off = offs.most_common(1)[0][0]
    sel = []
    covered = set()
    for j, (i, num) in enumerate(pages):
        end = pages[j + 1][0] if j + 1 < len(pages) else len(lines)
        bp = printed_page([x.strip() for x in lines[i + 1:end] if x.strip()])
        book = bp if bp is not None else num - off
        if bp is not None:
            covered.add(bp)
        if lo <= book <= hi:
            sel.append((num, i, end))
    return sel, rng


def clean_line(l):
    return l.replace('\u00ad', '').strip()


_SENT_Opener = re.compile(
    r'\s(?=(?:Can|Could|Will|Would|This|That|These|Those|There|When|Where|What|Who|Why|How|'
    r'My|Your|Her|His|Our|The|It|It’s|She|He|They|We|You|I|Please|Do|Does|Did|Is|Are|Was|Were|'
    r'Have|Has|Put|Look|Listen|Simon|Sue|Tim)\b[a-z ’\'-]*\s)')


_SENT_Opener = re.compile(
    r'\s(?=(?:Can|Could|Will|Would|This|That|These|Those|There|There’s|When|Where|What|Who|Why|How|'
    r'My|Your|Her|His|Our|The|It|It’s|She|He|They|They’re|We|We’re|You|You’re|I|I’m|Please|Do|Does|Did|Is|Are|Was|Were|'
    r'Have|Has|Had|Put|Look|Listen|Simon|Sue|Tim|One|In|At|On|Africa|Australia|America|Britain)\b[^.!?]*\s)')


def _trim_de(de):
    """Übersetzung bereinigen. WICHTIG: Schnitt an der ORIGINAL-Abfolge (Doppel-Leerzeichen
    trennen Entry von Beispielsatz), erst danach OCR-Leerzeichen kollabieren."""
    de = str(de or '').replace('\u00ad', '')
    # 1) Beispielsätze an Doppel-Leerzeichen-Grenze: Rest muss englisch aussehen
    m = re.search(r'^(.*?\S)\s{2,}(\S.*)$', de)
    if m:
        first, second = m.group(1).strip(), m.group(2).strip()
        looks_en = (not any(c in second for c in 'äöüßÄÖÜ')
                    and len(second.split()) >= 4
                    and (re.match(r"^(?:[A-Z][a-z]|The |A |An |I |You |We |They |It |In |At |On |"
                                  r"With |When |Is |Are |Was |Can |Could |Do |Does |Did |She |He |"
                                  r"My |There |That |What |Where |Please |One )", second)
                         or second[0].islower()))
        if looks_en:
            de = first
    de = de.split(' English:')[0]
    de = de.split(' German:')[0]
    de = re.split(r'\sstress:\s|\sadj:\s|\snoun:\s|\sverb:\s|\sadv:\s|\sopp:\s|\s+:(?=\s)', de)[0]
    # 3) OCR-Leerzeichen kollabieren
    de = re.sub(r'\s{2,}', ' ', de).strip()
    # 4) AE/BE-/F/L-Herkunft, ~-Sätze, '( auch:'-Ergänzungen
    de = re.split(r'\s=\s|\sF\s+|\sL\s+', de)[0]
    de = re.split(r'(?<![\wäöüß])~\S*\s*|(?<![\wäöüß])~\s', de)[0].rstrip()
    # Vertauschte OCR: 'anywhere. überall' -> führende englische Satzreste vor deutschem Wort weg
    m4 = re.match(r'^([A-Za-z][^\wäöüßÄÖÜ]*?[.!?])\s+((?:[^\s äöüßÄÖÜ]*[\wäöüßÄÖÜ])\S*)\b.*$', de)
    if m4 and any(c in m4.group(2) for c in 'äöüßÄÖÜ'):
        de = m4.group(2)
    m = re.search(r'\(\s*(?:auch|bes\b|pl\b|AE\b|BE\b|infml|formal|wörtl)', de)
    if m and m.start() > 3:
        de = de[:m.start()].rstrip()
    # Angeschlossene englische Beispiel-Fragmente am ENDE ('Pfosten a fence post',
    # 'Ur- The koala is ~ to Austra'): tail ohne Umlaute mit a/an/The...-Einstieg
    tail = re.search(r'[;,-]?\s+(?:(?:a|an|the|The|A|An)\s+)?[A-Za-z][^äöüßÄÖÜ]{3,80}$', de)
    if tail and not any(c in tail.group(0) for c in 'äöüßÄÖÜ'):
        inner = tail.group(0).lstrip(';,- ')
        if re.match(r'^(?:a|an|the|The|A|An|She|He|They|We|You|It|My|When|If|In|At|On)\b', inner) and len(inner.split()) >= 3:
            de = de[:tail.start()].rstrip(' ;,-').rstrip()
    # 5) Worttrennungen, Apostroph-Beispiele, Klammerreste
    de = re.sub(r'(\w)- (?=\w)', r'\1', de)
    # Apostroph-Formen mitten im Text (She's pointing ... / That's ...) = Satzrest
    ap2 = re.search(r'\s(?:She’s|He’s|It’s|That’s|They’re|We’re|You’re|I’m|There’s|Don’t|doesn’t)\s+.*$', de)
    if ap2 and len(de[:ap2.start()].strip().split()) >= 1 and not any(c in de[ap2.start():] for c in 'äöüßÄÖÜ'):
        de = de[:ap2.start()].rstrip(' ,;.')
    ap = re.search(r'\s[A-Z][a-z]{1,8}’[a-z]+\s+(?=(?:\S+\s){3,})', de)
    if ap and not any(c in de[ap.start():] for c in 'äöüßÄÖÜ'):
        de = de[:ap.start()].rstrip(' ,;.')
    if de.count('(') > de.count(')'):
        cut = de.rfind('(')
        de = de[:cut].rstrip(' ,;.')
    # führende '='-Leichen, Endfragmente ('Ur-', 'Mittelstress' OCR-Verkettungen)
    de = de.lstrip('= ').strip()
    de = re.sub(r'\s+={1,2}\s*$', '', de).strip()
    de = re.sub(r"(?<!\w)([A-ZÄÖÜ]?[a-zäöü]{1,3}-)(\s|$)", '', de).strip()
    de = re.sub(r'\bgrandGroß-\b|\bp\.50 = \b', '', de).strip()
    return de.strip()


def _final_ok(en, de):
    """Letzte Gütefilter-Klasse: offensichtliche OCR-Leichen verwerfen."""
    if not en or not de:
        return False
    en_t = en.strip(' .')
    if en in ('(to)', '…', 'English:', 'German:', 'F', 'L', 'F/L', 'AE', 'BE', 'AE/BE', 'e.g.', 'i.e.'):
        return False
    de_t = de.strip(' =–-')
    # OCR-Wiederholung ('minute = minute') — ABER Cognates mit gleicher Schreibung sind LEGAL
    # (religion = Religion, chaos = Chaos): nur verwerfen bei reinem Kleinbuchstaben-Duplikat
    if de_t and de_t.casefold() == en.casefold() and not any(c.isupper() for c in de):
        return False
    # Marker-EN ('pl', 'pp', 'AE', Grammar-Icons) sind keine Vokabel-EN-Seite
    if en.casefold() in ('pl', 'pp', 'ae', 'be', 'infml', 'fml', 'no pl', 'pl.', 'e.g', 'ie', 'sb', 'sth'):
        return False
    if de_t.casefold() == en_t.casefold().replace('.', '').strip() and en != de and not de[:1].isupper():
        return False
    if de_t.casefold() in en.casefold() and not any(c in de_t for c in 'äöüßÄÖÜ') and not de[:1].isupper():
        return False
    # deutschlos + satzartig + kapitalisiert = Fragment — ABER legitime kurze deutsche
    # Sätze ohne Umlaut ('Danke.', 'Geh nicht.', 'Viel Spass.', 'Gute Nacht.') NICHT werfen.
    if (not any(c in de for c in 'äöüßÄÖÜ') and len(de.split()) <= 2
            and de.endswith('.') and de[0:1].isupper()):
        de_words = re.findall(r'[A-Za-zÄÖÜäöüß’]+', de)
        # verwerfen nur, wenn ein Wort Klar-ENGLISCH ist (EN-Stopwörter/typische Fragment-Wörter)
        en_lex = ('the', 'and', 'please', 'wait', 'you', 'your', 'for', 'with', 'when', 'what',
                  'how', 'can', 'she', 'he', 'they', 'we', 'it', 'this', 'that', 'there', 'here',
                  'have', 'has', 'are', 'was', 'were', 'is', 'think', 'like', 'see', 'look',
                  'listen', 'say', 'sorry', 'hello', 'bye', 'thanks', 'welcome', 'well', 'good', 'nice')
        hits = [w for w in de_words if w.lower().strip('’.,') in en_lex]
        if hits and not any(c in de for c in 'äöüßÄÖÜ'):
            return False
    # Grammar-Box-Fragmente: 'In Great Britain the word ...'
    if en.startswith(('In ', 'At ', 'On ')) and not any(c in de for c in 'äöüßÄÖÜ'):
        return False
    if ' 73 ' in f' {en} {de} ' or f' {en} ' == ' 73 ':
        return False
    if en.startswith(('(=', 'wörtlich:', 'stress:', 'adj:', 'noun:')):
        return False
    return True


def is_garbage(l):
    if not l or l in SKIP_EXACT:
        return True
    if SPelled.match(l):
        return True
    if '•' in l:
        return True
    if re.fullmatch(r'\[.+\]', l):
        return True  # Lautschrift-Übersichtszeile
    if re.fullmatch(r'[a-zɪʊʌɑɒɔæθðʃʒˌˈːə]+[a-z ]*', l.replace(' ', '')) and len(l) < 3:
        return True
    return False


def _en_plausible(en):
    """Sehr leichter Test, ob das englische Feld nicht reiner Quatsch ist."""
    en = (en or '').strip()
    if not en:
        return False
    if any(c in en for c in 'äöüßÄÖÜ'):
        return False
    if re.match(r'^(?:Das |Der |Die |Ein |Eine |Ich |Wir |Sie |Er |Es |Am |Im |Mit |Für |Zuerst |Zuhause)', en):
        return False
    return True


END_IPA_RE = re.compile(r'^\s*(?P<en>\S.*?)\s*\[(?P<ipa>[^\]]+)\]\s*[,;.:]?\s*$')


DE_STOP = re.compile(
    r'(^|\s|])(?:der|die|das|ein|eine|ich|du|er|wir|und|oder|mit|von|zum|zur|'
    r'für|auf|aus|bei|nach|jn|jm|etwas|sich|nicht|auch|wieder|sind|ist|hast|habe|'
    r'zu Hause|daheim|ihre|ihr|ihm|ihnen|uns|seine|seiner|dem|den|des|am|im)(\s|,|\.|$|/|:|\))')


def _de_stopwords(seg_low):
    return DE_STOP.search(seg_low or '')


def _split_mixed_line(en, de):
    """GAP-Zeilen mit Misch-Segmenten: 'Maya is' + 'at home .  daheim, zu Hause'
    → en='Maya is at home.', de='daheim, zu Hause'. Segmente = ORIGINAL-Doppel-LZ-Gruppen;
    alles bis zum ersten DE-Segment gehört zum EN. Ruft mit RAW-de (Doppel-LZ intakt) auf!"""
    segs = re.split(r'\s{2,}', str(de or '').replace('\r', ''))
    if len(segs) <= 1:
        return en, re.sub(r'\s+', ' ', str(de or '')).strip()
    first_de = None
    for i, s in enumerate(segs):
        sl = s.lower().strip()
        has_uml = any(c in s for c in 'äöüßÄÖÜ')
        is_de = has_uml or _de_stopwords(sl) is not None
        if is_de:
            first_de = i
            break
    if first_de is None or first_de == 0:
        return en, re.sub(r'\s{2,}', ' ', de).strip()
    en_new = en + ' ' + ' '.join(segs[:first_de])
    de_new = ' '.join(segs[first_de:])
    return re.sub(r'\s+', ' ', en_new).strip(), re.sub(r'\s{2,}', ' ', de_new).strip(' ,;')


def _de_has_german(de):
    """Deutsches Kennzeichen? Umlaut/ß, de-Stopwörter, oder typische de-Endung."""
    if any(c in de for c in 'äöüßÄÖÜ'):
        return True
    dl = de.lower().strip()
    if re.search(r'(?:^|[\s,./:!?\])])(?:der|die|das|ein|eine|ich|du|er|sie|es|wir|und|oder|mit|von|zum|zur|für|auf|aus|bei|nach|jn|jm|sich|nicht|auch|wieder|ist|sind|habe|hast|hat|zu|hause|daheim|um|im|an|als|wie|so|guten|gute|morgen|abend|nacht|danke|bitte|dank|ja|nein|hallo|tschüs|tschüss|viel|spass|spaß|okay|entschuldigung|mehr|man|manchmal|nur|noch|schon|immer|alle|alles|werden|wird|kann|kannst|muss|müssen|soll|will|möchte)(?:$|[\s,./:!?\)\]])', dl):
        return True
    words = dl.split()
    if words and re.search(r'(?:ung|heit|keit|schaft|lich|isch|bar|chen|tum)(?:s)?$|^[a-zäöüß]+(?=n$)', words[-1]):
        return True
    return False


def _sanitize_pair(en, de):
    """Letzte Reinigung je Paar: OCR-Restklassen säubern oder Entry verwerfen.
    Rückgabe (en, de) oder None."""
    # leading-private-use (\ue08e Bindebogen) und übrige Sonderzeichen aus EN entfernen
    en = re.sub(r'[\ue000-\uf8ff]', '', str(en or '')).strip()
    en = re.sub(r'\s+', ' ', en).strip()
    de = str(de or '').replace('\ue08e', 'ˌ')
    # Betonung:/Antonym-Icon(73)/£$-Anhänge
    de = re.split(r'\sBetonung:\s|\s73\s|\s£.*$|\s\$\d.*$', de)[0]
    # führende (bes. AE)/(AE)/(BE)-Marker
    de = re.sub(r'^\(\s*(?:bes\.?\s+)?(?:AE|BE)\s*\)\s*', '', de)
    # Worttrennung + Doppel-Leerzeichen
    de = re.sub(r'(\w)- (?=\w)', r'\1', de)
    de = re.sub(r'\s{2,}', ' ', de).strip()
    # Wiederholung des en-Worts am de-Ende ('Schatten shadow')
    if en.casefold() in ('shadow',):
        pass
    low, enlow = de.casefold(), en.casefold().strip('() .')
    if enlow and low.endswith(' ' + enlow):
        de = de[:len(low) - (len(enlow) + 1)].rstrip()
    # angehängtes englisches Satzfragment: [A-Z]-Start + 2–6 reine ASCII-Wörter + Endpunkt
    m = re.search(r'^(.*?\S)\s+((?:[A-Z][A-Za-z’\'-]*)(?:\s+[A-Za-z’\'£$.,()-]+){1,6}[.!?])$', de)
    if m and not any(c in m.group(2) for c in 'äöüßÄÖÜ'):
        de = m.group(1).rstrip()
    # lowercase-Glue-Wörter am Ende ('please.', 'und so weiter'-Varianten bleiben)
    m2 = re.search(r'^(.*?\S)\s+(?:please|Wait|Aha|sorry)\W*$', de, re.I)
    if m2 and len(m2.group(1)) >= 3 and 'usw' not in (m2.group(1)[-10:].lower()):
        de = m2.group(1).rstrip(' ,')
    de = de.strip(' ,;')
    # Satzförmige EN-Seite + nicht-deutsche DE-Seite = Beispielsatz-Leiche → verwerfen
    en_sentence = (en.rstrip().endswith(('?', '!', '.')) and not en.rstrip().endswith('…')) \
        or '. ' in en or len(en.split()) > 4
    if en_sentence and not _de_has_german(de):
        return None
    # Verwerfen: smashed Compound ('leichte Nachmittagsoder'), leere Übersetzung,
    # reine Marker-Übersetzung, en wie invertierter deutscher Satz, Seiten-/Header-Leichen
    # plus: EN-Seite mit deutschem Satz/Fragment (invertierte Zeile), Seitenzahl-Zeilen
    if re.fullmatch(r'\d+', en) or re.match(r'^\d+\s+hundred', en):
        return None
    if _de_has_german(en) and not _de_has_german(de):
        return None
    if not de or de in ('…', '(no pl)', 'pl', '(pl)', '-'):
        return None
    if re.search(r'(?:^|\s)[a-zäöüß]{8,}oder\b', de) or re.search(r'^[a-zäöüß]{8,}\.$', de):
        return None
    if re.match(r'^(?:Das |Der |Die |Ein |Eine |Ich |Wir |Sie |Er |Es |Am |Im |Mit |Für |In the first place|Er hat )', en) \
       and len(en.split()) >= 4 and not re.search(r'\((?:to|pl|AE|BE|infml|no|fml|kort|brit)|\bpl\)', en):
        return None
    if re.match(r'^(?:p{1,3}\.\s?\d|pp?\.\s?\d|STEP \d|Part [AB]|Units? \d|Revision|Grammar|Practice|Skills|Dictionary|Irregular|Content|Writing)', en):
        return None
    if enlow in ('the tube',) and de == '(no pl)':
        pass  # legitime Angabe, bleibt
    return (en, de)


def book_toc_titles(txt_path):
    """Unit-Titel aus dem Vorderbuch. Zwei Muster:
    (a) 'Unit N  Titel  Seiten' in einer Zeile, (b) 'Unit N' + Titel auf der nächsten Zeile."""
    lines = load_lines(txt_path)[:450]
    titles = {}
    for i, l in enumerate(lines):
        l = clean_line(l)
        m = re.match(r'^(?:Unit )?(\d{1,2})\s{2,}([A-Z][^•\[\]{}|]{2,42}?)(?:\s{2,}\d{1,3})?\s*$', l)
        if not m:
            m1 = re.match(r'^(?:Units?)\s+(\d{1,2})\s*$', l)
            if m1:
                nxt = ''
                for l2 in lines[i + 1:i + 6]:
                    t2 = clean_line(l2)
                    if not t2 or re.fullmatch(r'\d{1,3}', t2):
                        continue
                    if re.match(r'^[A-Z][^•\[\]{}|]{2,42}$', t2) and not any(c.isdigit() for c in t2):
                        nxt = re.sub(r'\s{2,}', ' ', t2).strip()
                    break
                if nxt:
                    m2 = re.match(r'^(\d{1,2})$', m1.group(1)) or m1
                    num = int(m1.group(1))
                    if num <= 12 and nxt not in ('Revision', 'Exercises'):
                        if num not in titles or len(nxt) > len(titles[num]):
                            titles[num] = nxt
                continue
            continue
        num = int(m.group(1))
        title = re.sub(r'\s{2,}', ' ', m.group(2)).strip()
        if num <= 12 and title and not any(c.isdigit() for c in title) and title not in ('Revision', 'Exercises'):
            if num not in titles or len(title) > len(titles[num]):
                titles[num] = title
    return titles


def parse_band(txt_path):
    lines = load_lines(txt_path)
    vocab, rng = vocab_pages(lines)
    if not vocab:
        return None, ['KEINE Vocabulary-Seiten gefunden']
    report = [f'{len(vocab)} Vocab-Seiten' + (f' (Buchseiten {rng[0]}–{rng[1]})' if rng else '')]

    by_num = {}   # unit-num -> chapter dict (Merge über Seitenfragmente)
    order = []
    cur_num = None

    def ch_for(num, title=''):
        nonlocal cur_num
        cur_num = num
        noise = title in ('', 'Vocabulary')
        if num not in by_num:
            by_num[num] = {'num': num, 'title': '' if noise else title, 'words': []}
            order.append(num)
        elif title and not noise and (not by_num[num]['title'] or by_num[num]['title'] == 'Vocabulary'):
            by_num[num]['title'] = title
        return by_num[num]

    skipped = 0
    for _page, start, end in vocab:
        # Unit für diese Seite: erst Seitenkopf-Muster (Unit N ...), sonst weiterführen
        hdr_unit = None
        head_txt = [re.sub(r'\s{2,}', ' ', x.strip()) for x in lines[start + 1:start + 8]]
        for h in head_txt:
            umh = re.match(r'^Unit (\d+)\b', h)
            if umh:
                hdr_unit = int(umh.group(1))
                break
            if re.search(r'Here we go', h) and 'Vocabulary' in h:
                hdr_unit = 0
                break
        if hdr_unit is not None:
            ch = ch_for(hdr_unit, 'Here we go' if hdr_unit == 0 else '')
        elif cur_num is not None:
            ch = by_num[cur_num]
        else:
            continue  # Entries ohne bekannte Unit verwerfen
        for raw in lines[start + 1:end]:
            l = clean_line(raw)
            if is_garbage(l) or re.fullmatch(r'\d{1,3}', l.strip()):
                continue
            # Nur-EN-Zeilen (Beispielsätze ohne deutsche Übersetzung) → verwerfen
            um_pre = UNIT_RE.match(l)
            if (not um_pre and not HERE_RE.match(l) and not IPA_RE.match(l)
                    and not END_IPA_RE.match(l) and not _de_has_german(l)
                    and re.search(r'[a-z]’?[a-z]*\s+[a-z]', l)
                    and l.rstrip().endswith(('.', '!', '?', '…'))):
                skipped += 1
                continue
            um = UNIT_RE.match(l)
            hm = HERE_RE.match(l)
            if um and not IPA_RE.match(l):
                title = re.sub(r'\s{2,}', ' ', um.group(2)).strip()
                ch = ch_for(int(um.group(1)), title)
                continue
            if hm:
                ch = ch_for(0, 'Here we go')
                continue

            im = IPA_RE.match(l)
            added = False
            if im and len(im.group('ipa')) <= 30 and im.group('en'):
                en = re.sub(r'\s+', ' ', im.group('en')).strip()
                # MEHRERE IPA-Gruppen ('[hæv], [həv]  haben'): de = Text nach der LETZTEN ]
                de_raw = im.group('de')
                if de_raw.startswith((']', ', [', '], [', ',  [')):
                    all_ipa = re.findall(r'\[[^\]]+\]', l)
                    last_close = l.rfind(']')
                    en2 = re.sub(r'^(.*?\S)\s*\[[^\]]*\].*$', r'\1', l).strip()
                    en = re.sub(r'\s+', ' ', en2).strip()
                    de_raw = l[last_close + 1:]
                pair = _sanitize_pair(en, _trim_de(de_raw))
                if pair and _final_ok(*pair) and len(pair[0].split()) <= 6 and len(pair[1].split()) <= 8:
                    ch['words'].append(list(pair))
                    added = True
            if not added:
                gm = GAP_RE.match(l)
                if gm and len(gm.group('en')) <= 40:
                    en0 = re.sub(r'\s+', ' ', gm.group('en')).strip()
                    # GAP mit IPA im de-Feld = eigentlich END_IPA-Zeile (en brach auseinander)
                    if re.search(r'\[[^\]]+\]\s*[,;.:]?\s*$', gm.group('de')):
                        pass  # unten im END_IPA-Zweig behandeln
                    else:
                        en1, de1 = _split_mixed_line(en0, gm.group('de'))
                        pair = _sanitize_pair(en1, _trim_de(de1))
                        if pair and _final_ok(*pair) and len(pair[0].split()) <= 6 and len(pair[1].split()) <= 8:
                            ch['words'].append(list(pair))
                            added = True
            if not added:
                # ZEILENEND-IPA: 'phrase  [IPA]' OHNE de — Lösung auf der FOLGEZEILE
                em = END_IPA_RE.match(l)
                if em:
                    en = re.sub(r'\s+', ' ', em.group('en')).strip()
                    if en and _en_plausible(en):
                        ch['words'].append([en, '\u0000PENDING\u0000'])
                    continue
            if not added and ch['words']:
                last = ch['words'][-1]
                # PENDING-Entry: deutsche Folgzeile einsetzen
                if last[1] == '\u0000PENDING\u0000':
                    cont = clean_line(raw).strip()
                    # IPA-Varianten/Verweiszeilen ('(= they are [..] )', '[dəʊnt]') überspringen
                    # und PENDING für die echte deutsche Zeile offenhalten
                    if re.search(r'\[[^\]]+\]', cont) or cont.startswith('(=') or cont.startswith('['):
                        continue
                    looks_de = (any(c in cont for c in 'äöüßÄÖÜ')
                                or cont.startswith(('jn.', 'jm.', 'der ', 'die ', 'das ', 'ein ', 'eine ',
                                                    '-', '(', 'sich', 'ihr ', 'ihre ', 'er ', 'es ', 'wir ',
                                                    'mein ', 'meine ', 'dein ', 'deine ', '( to', '(to', 'wieder')))
                    # Grammatik-Info-Zeilen ('pl trophies', 'pp 40/41', 'no pl') überspringen,
                    # PENDING bleibt für die nächste Zeile offen
                    if re.match(r'^(?:pl|pp?|no pl|AE|BE|infml|fml|pl\.)\b', cont) and not looks_de:
                        continue
                    if cont and not is_garbage(cont) and not re.fullmatch(r'\d{1,3}', cont) and looks_de:
                        last[1] = cont
                    else:
                        last[1] = '\u0000DROP\u0000'  # keine deutsche Lösung gefunden
                    continue
                # normale Continuation
                cont = re.sub(r'\s+', ' ', l)
                # Verwerfen: leading ')' nach '(' - Fragment wie '(= they are' + ')'
                if ch['words'] and re.match(r'^\)\s*$|^$|^,?\s*(=\s*|~)', cont):
                    skipped += 1
                    continue
                if len(cont) <= 60:
                    prev = last[1].rstrip()
                    if prev.endswith((',', '/', '-', '(')) or prev == '':
                        last[1] = prev + ('' if prev.endswith('(') else ' ') + cont
            else:
                pass
        # Ende Seite

    # PENDING/DROP-Reste aufräumen + HARTE END FILTER: nie IPA/Brackets als Übersetzung
    for ch in by_num.values():
        w2 = []
        for en, de in ch['words']:
            if de == '\u0000PENDING\u0000':
                de = ''  # nie gelöst
            if not de or de == '\u0000DROP\u0000':
                continue
            # IPA im de-Feld = kaputtes Entry (Lautschrift ist KEINE Übersetzung)
            if re.search(r'\[[^\]]+\]', de):
                continue
            # IPA im en-Feld (einige Konstrukt-Zeichen wie [ˌ]) — nur verwerfen bei reinem Bracket
            if re.fullmatch(r'\[[^\]]*\]', en.strip()):
                continue
            if _final_ok(en, de):
                w2.append([en, de])
        ch['words'] = w2

    merged = [by_num[n] for n in sorted(order, key=lambda x: (x == 0, x))]
    # Dedup pro Kapitel (en casefold)
    for ch in merged:
        seen = set()
        w = []
        for en, de in ch['words']:
            k = en.casefold()
            if k in seen:
                continue
            seen.add(k)
            w.append([en, de])
        ch['words'] = w
        # Unit-Titel aus dem Buch-Inhaltsverzeichnis ergänzen
    try:
        titles = book_toc_titles(txt_path)
    except Exception:
        titles = {}
    for ch in merged:
        if ch['num'] == 0:
            ch['title'] = 'Here we go'
        elif not ch['title'] or ch['title'] == 'Vocabulary':
            ch['title'] = titles.get(ch['num'], f'Unit {ch["num"]}')
    report.append(f'skipped garbage: {skipped}')
    return {'version': 1, 'chapters': merged}, report


def main():
    out_summary = []
    for band, book in BANDS.items():
        src = f'import/Access_G9_Band{band}_text.txt'
        data, report = parse_band(src)
        if data is None:
            print(f'BAND {band}: FEHLER {report}')
            continue
        total = sum(len(c['words']) for c in data['chapters'])
        print(f'BAND {band} ({book}): {len(data["chapters"])} Kapitel, {total} Vokabeln')
        for c in data['chapters']:
            print(f'   Ch {c["num"]:>2} {c["title"][:40]:40} {len(c["words"]):4} Wörter')
        with open(f'seed/{book}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        out_summary.append((book, len(data['chapters']), total))
    print('\nFERTIG:', out_summary)


if __name__ == '__main__':
    main()