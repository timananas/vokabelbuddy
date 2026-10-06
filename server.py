#!/usr/bin/env python3
"""Vokabelbuddy – LAN-only Vokabeltrainer (Access 1/2, Cornelsen).

Python-Stdlib only (kein pip), Muster wie Planbrett-Server.
Daten: Kapitel als JSON (ein File je Buch) in DATA_DIR; seed/<book>.json
wird beim ersten Zugriff einmalig kopiert. Lernstatistik je Kid in stats.json
(überlebt Container-Neustart). Antwortprüfung serverseitig über einen
Session-Cache (nonce -> Fragen mit Lösungen, TTL 2h) – Lösungen liegen nie
im ausgelieferten Quiz.
"""
import csv
import io
import json
import os
import random
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = '/app/data' if os.path.isdir('/app/data') else os.path.join(ROOT, 'data')
SEED_DIR = os.path.join(ROOT, 'seed')
VERSION = '2026-10-06.1'
PORT = 8894

BOOKS = [
    {'id': 'access1', 'name': 'Access 1', 'desc': 'Cornelsen · Klasse 5'},
    {'id': 'access2', 'name': 'Access 2', 'desc': 'Cornelsen · Klasse 6'},
    {'id': 'access3', 'name': 'Access 3', 'desc': 'Cornelsen · Klasse 7'},
    {'id': 'access4', 'name': 'Access 4', 'desc': 'Cornelsen · Klasse 8'},
    {'id': 'access5', 'name': 'Access 5', 'desc': 'Cornelsen · Klasse 9'},
    {'id': 'access6', 'name': 'Access 6', 'desc': 'Cornelsen · Klasse 10'},
]
KIDS = [
    {'id': 'luis', 'name': 'Luis', 'color': '#5b8def'},
    {'id': 'carlotta', 'name': 'Carlotta', 'color': '#3fb27f'},
]
DEFAULT_BOOKS = {'luis': 'access2', 'carlotta': 'access1'}

_lock = threading.RLock()
_sessions = {}  # nonce -> {'created': ts, 'questions': {f'{qid}|{direction}': question}}

SESSION_TTL_S = 2 * 3600
MAX_SESSIONS = 500


def _gc_sessions(now):
    stale = [k for k, v in _sessions.items() if now - v['created'] > SESSION_TTL_S]
    for k in stale:
        _sessions.pop(k, None)
    if len(_sessions) > MAX_SESSIONS:
        oldest = sorted(_sessions.items(), key=lambda kv: kv[1]['created'])
        for k, _v in oldest[:len(_sessions) - MAX_SESSIONS]:
            _sessions.pop(k, None)


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _norm(s):
    return str(s or '').strip()


def _book_ids():
    return {b['id'] for b in BOOKS}


def _kid_ok(kid):
    return any(k['id'] == kid for k in KIDS)


def _qdata(book):
    """Kapitel-JSON: data/<book>.json, sonst Seed einmalig kopieren, sonst leer."""
    with _lock:
        data = _load_json(os.path.join(DATA_DIR, f'{book}.json'), None)
        if data is None:
            seed = _load_json(os.path.join(SEED_DIR, f'{book}.json'), None)
            if seed is not None and isinstance(seed.get('chapters'), list):
                data = seed
                _save_json(os.path.join(DATA_DIR, f'{book}.json'), data)
        if not isinstance(data, dict) or not isinstance(data.get('chapters'), list):
            data = {'version': 1, 'chapters': []}
        return data


def _stats():
    with _lock:
        st = _load_json(os.path.join(DATA_DIR, 'stats.json'), None)
        if not isinstance(st, dict):
            st = {'version': 1, 'kid_seen': {}}
        if not isinstance(st.get('kid_seen'), dict):
            st['kid_seen'] = {}
        for k in list(st['kid_seen'].keys()):
            if not isinstance(st['kid_seen'][k], dict):
                st['kid_seen'][k] = {}
        return st


def _chapter_stats(st, kid):
    """Aggregat je 'book|chapter' -> {n, right, streak}."""
    agg = {}
    for qid, s in st['kid_seen'].get(kid, {}).items():
        parts = str(qid).split('|')
        if len(parts) < 3:
            continue
        key = f'{parts[0]}|{parts[1]}'
        a = agg.setdefault(key, {'n': 0, 'right': 0, 'streak': 0})
        a['n'] += int(s.get('n', 0) or 0)
        a['right'] += int(s.get('s', 0) or 0)
        a['streak'] = max(a['streak'], int(s.get('streak', 0) or 0))
    return agg


def _pool(book, chapters, from_n=None, to_n=None):
    """Wörter der gewählten Kapitel (dedupliziert, casefold).
    pos = 1-basierte Position INNERHALB des Kapitels (Buch-Reihenfolge, für Anzeige).
    gpos = fortlaufende Nummer über ALLE gewählten Kapitel (buchsortiert);
    from_n/to_n filtern auf gpos — bei einem Kapitel identisch mit pos."""
    data = _qdata(book)
    chs = sorted((ch for ch in data['chapters'] if str(ch.get('num')) in chapters),
                 key=lambda c: int(c['num']) if str(c.get('num', 0)).isdigit() else 999)
    pool = []
    seen = set()
    gpos = 0
    for ch in chs:
        chnum = str(ch.get('num'))
        words = ch.get('words') if isinstance(ch.get('words'), list) else []
        for pos, w in enumerate(words, 1):
            if not isinstance(w, (list, tuple)) or len(w) < 2:
                continue
            en, de = _norm(w[0]), _norm(w[1])
            if not en or not de:
                continue
            gpos += 1
            if from_n and gpos < from_n:
                continue
            if to_n and gpos > to_n:
                continue
            key = (en.casefold(), de.casefold())
            if key in seen:
                continue
            seen.add(key)
            pool.append({'ch_ix': data['chapters'].index(ch), 'num': chnum, 'pos': pos,
                         'gpos': gpos, 'en': en, 'de': de,
                         'qid': f'{book}|{chnum}|{en.casefold()}'})
    return pool


def _build_question(item, dpool, direction, rng):
    en, de = item['en'], item['de']
    if direction == 'de2en':
        prompt, answer, kind = de, en, 'en'
    else:
        prompt, answer, kind = en, de, 'de'

    cf = answer.casefold()
    cands = []
    for other in dpool:
        if other is item:
            continue
        v = other[kind]
        if not v or v.casefold() == cf:
            continue
        cands.append((other['ch_ix'] == item['ch_ix'], v))
    # gleiches Kapitel bevorzugt (stable sort über zufällige Reihenfolge)
    rng.shuffle(cands)
    cands.sort(key=lambda c: not c[0])

    picked, used = [], {cf}
    for _same, v in cands:
        k = v.casefold()
        if k in used:
            continue
        used.add(k)
        picked.append(v)
        if len(picked) == 3:
            break

    choices = [answer] + picked
    rng.shuffle(choices)
    return {
        'id': item['qid'],
        'direction': direction,
        'chapter': item['num'],
        'pos': item['pos'],
        'prompt': prompt,
        'choices': choices,
        'answer': answer,
    }


def _make_quiz(book, kid, chapters, qtype, count, nonce, from_n=None, to_n=None):
    pool = _pool(book, chapters, from_n, to_n)
    if not pool:
        return None
    # Distraktor-Pool: die KAPITEL ungefiltert (auch bei Bereich 1–1 volle 4 Optionen)
    dpool = _pool(book, chapters)
    st = _stats()
    seen_map = st['kid_seen'].get(kid, {})
    rng = random.Random()

    now = time.time()
    with _lock:
        _gc_sessions(now)
        _sessions[nonce] = {'created': now, 'questions': {}}

    def weight_of(entry):
        n = int(entry.get('n', 0) or 0)
        s = int(entry.get('s', 0) or 0)
        streak = int(entry.get('streak', 0) or 0)
        if n == 0:
            return 3
        if s == 0 or streak < 3:
            return 2
        return 1

    scored = [(weight_of(seen_map.get(it['qid'], {})), rng.random(), it) for it in pool]
    scored.sort(key=lambda t: (-t[0], t[1]))
    count = max(1, min(int(count or 12), 30, len(scored)))
    dirs = (['en2de', 'de2en'] if qtype == 'both'
            else [qtype] if qtype in ('en2de', 'de2en')
            else ['en2de', 'de2en'])

    out = []
    for i, (_w, _r, item) in enumerate(scored[:count]):
        d = dirs[i % len(dirs)]
        q = _build_question(item, dpool, d, rng)
        with _lock:
            _sessions[nonce]['questions'][q['id'] + '|' + d] = q
        out.append({k: q[k] for k in ('id', 'direction', 'chapter', 'pos', 'prompt', 'choices')})
    return out


def _record(kid, qid, correct):
    with _lock:
        st = _stats()
        seen = st['kid_seen'].setdefault(kid, {})
        entry = seen.setdefault(qid, {'n': 0, 's': 0, 'streak': 0})
        entry['n'] = int(entry.get('n', 0) or 0) + 1
        if correct:
            entry['s'] = int(entry.get('s', 0) or 0) + 1
            entry['streak'] = min(3, int(entry.get('streak', 0) or 0) + 1)
        else:
            entry['streak'] = 0
        entry['last'] = datetime.now().strftime('%Y-%m-%d')
        _save_json(os.path.join(DATA_DIR, 'stats.json'), st)


def _parse_pairs(lines):
    """'english;deutsch'-Zeilen -> [[en, de]] (CSV-Parser, Anführungszeichen ok)."""
    out, errors = [], 0
    text = str(lines or '').replace('\r\n', '\n').replace('\r', '\n')
    for row in csv.reader(io.StringIO(text), delimiter=';', quotechar='"'):
        cells = [c.strip() for c in row]
        if not cells or all(not c for c in cells):
            continue
        if len(cells) < 2 or not cells[0] or not cells[1]:
            errors += 1
            continue
        out.append([cells[0], cells[1]])
    return out, errors


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print(f'{datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}', flush=True)

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 2_000_000:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            pass
        try:
            return {k: v[0] for k, v in parse_qs(raw.decode('utf-8'), keep_blank_values=True).items()}
        except Exception:
            return {}

    def _static(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            p = os.path.join(ROOT, 'index.html')
            if os.path.exists(p):
                self._send(200, open(p, 'rb').read(), 'text/html; charset=utf-8')
            else:
                self._send(200, b'<h1>Vokabelbuddy</h1><p>index.html fehlt</p>', 'text/html; charset=utf-8')
            return True
        return False

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        r = u.path
        try:
            if r == '/api/health':
                self._json({'ok': True, 'version': VERSION})
            elif r == '/api/meta':
                self._json({'version': VERSION, 'books': BOOKS, 'kids': KIDS,
                            'default_book': DEFAULT_BOOKS})
            elif r == '/api/chapters':
                self.api_chapters(q)
            elif r == '/api/quiz':
                self.api_quiz(q)
            elif r == '/api/answer':
                self.api_answer(q, {})
            elif r == '/api/stats':
                self.api_stats(q)
            elif r == '/api/export':
                self.api_export(q)
            elif r == '/api/words':
                self.api_words(q)
            elif self._static():
                pass
            else:
                self._send(404, b'not found', 'text/plain; charset=utf-8')
        except BrokenPipeError:
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._json({'error': str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path)
        r = u.path
        try:
            if r == '/api/answer':
                self.api_answer(parse_qs(u.query), self._body())
            elif r == '/api/chapter/save':
                self.api_chapter_save(self._body())
            elif self._static():
                pass
            else:
                self._send(404, b'not found', 'text/plain; charset=utf-8')
        except BrokenPipeError:
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._json({'error': str(e)}, 500)
            except Exception:
                pass

    # ---- API ----
    def api_chapters(self, q):
        book = (q.get('book') or [''])[0]
        kid = (q.get('kid') or [''])[0]
        if book not in _book_ids() or not _kid_ok(kid):
            self._json({'error': 'book/kid unbekannt'}, 400)
            return
        data = _qdata(book)
        agg = _chapter_stats(_stats(), kid)
        chapters = []
        for ch in data['chapters']:
            key = f'{book}|{ch.get("num")}'
            a = agg.get(key, {})
            chapters.append({
                'num': ch.get('num'), 'title': ch.get('title', ''),
                'count': len(ch.get('words') or []),
                'seen_n': a.get('n', 0), 'seen_right': a.get('right', 0),
                'best_streak': a.get('streak', 0),
            })
        chapters.sort(key=lambda c: int(c['num']) if str(c['num']).isdigit() else 999)
        self._json({'book': book, 'kid': kid, 'chapters': chapters})

    def api_quiz(self, q):
        book = (q.get('book') or [''])[0]
        kid = (q.get('kid') or [''])[0]
        nonce = (q.get('nonce') or [''])[0]
        chapters = {c for c in ((q.get('chapters') or [''])[0].split(',')) if c}
        qtype = (q.get('type') or ['both'])[0]
        from_n = to_n = None
        try:
            from_n = int((q.get('from') or [''])[0] or 0) or None
            to_n = int((q.get('to') or [''])[0] or 0) or None
        except ValueError:
            pass
        if from_n and to_n and from_n > to_n:
            from_n, to_n = to_n, from_n
        if book not in _book_ids() or not _kid_ok(kid) or not (6 <= len(nonce) <= 64):
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        if not chapters:
            self._json({'error': 'Kein Kapitel gewählt'}, 400)
            return
        qs = _make_quiz(book, kid, chapters, qtype, (q.get('count') or ['12'])[0], nonce, from_n, to_n)
        if qs is None:
            self._json({'error': 'Keine Vokabeln in diesem Bereich'}, 404)
            return
        self._json({'book': book, 'kid': kid, 'kind': qtype, 'questions': qs})

    def api_answer(self, q, body):
        def pick(k):
            v = body.get(k)
            return str(v if v not in (None, '') else (q.get(k) or [''])[0])

        kid = pick('kid')
        qid = pick('qid')
        direction = pick('direction') or 'en2de'
        nonce = pick('nonce')
        choice = pick('choice')
        if not _kid_ok(kid) or not qid or not (6 <= len(nonce) <= 64):
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        sess = _sessions.get(nonce)
        question = sess['questions'].get(qid + '|' + direction) if sess else None
        if question is None:
            self._json({'error': 'Quiz-Sitzung abgelaufen – bitte neues Quiz starten'}, 404)
            return
        try:
            idx = int(choice)
        except (TypeError, ValueError):
            idx = -1
        correct = 0 <= idx < len(question['choices']) and question['choices'][idx] == question['answer']
        _record(kid, qid, correct)
        self._json({'correct': correct, 'answer': question['answer'], 'ok': True})

    def api_stats(self, q):
        kid = (q.get('kid') or [''])[0]
        st = _stats()
        if kid:
            if not _kid_ok(kid):
                self._json({'error': 'kid unbekannt'}, 400)
                return
            self._json({'kid': kid, 'chapters': _chapter_stats(st, kid)})
            return
        self._json({'by_kid': {k['id']: _chapter_stats(st, k['id']) for k in KIDS}})

    def api_export(self, q):
        book = (q.get('book') or [''])[0]
        if book not in _book_ids():
            self._json({'error': 'book unbekannt'}, 400)
            return
        body = json.dumps(_qdata(book), ensure_ascii=False, indent=1).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="vokabeln-{book}.json"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_words(self, q):
        """Wortlisten je Kapitel mit Positions-Index (Sichtprüfung/Tests)."""
        book = (q.get('book') or [''])[0]
        if book not in _book_ids():
            self._json({'error': 'book unbekannt'}, 400)
            return
        only = {c for c in ((q.get('chapters') or [''])[0].split(',')) if c}
        data = _qdata(book)
        out = []
        for ch in data['chapters']:
            num = str(ch.get('num'))
            if only and num not in only:
                continue
            words = []
            for pos, w in enumerate(ch.get('words') or [], 1):
                if not isinstance(w, (list, tuple)) or len(w) < 2:
                    continue
                en, de = _norm(w[0]), _norm(w[1])
                if en and de:
                    words.append({'pos': pos, 'en': en, 'de': de})
            out.append({'num': num, 'title': ch.get('title', ''), 'count': len(words), 'words': words})
        out.sort(key=lambda c: int(c['num']) if str(c['num']).isdigit() else 999)
        self._json({'book': book, 'chapters': out})

    def api_chapter_save(self, body):
        book = _norm(body.get('book'))
        if book not in _book_ids():
            self._json({'error': 'book unbekannt'}, 400)
            return
        action = _norm(body.get('action')) or 'save'
        with _lock:
            data = _qdata(book)
            chapters = data.get('chapters') or []
            if action == 'delete':
                num = _norm(body.get('num'))
                before = len(chapters)
                chapters = [ch for ch in chapters if str(ch.get('num')) != num]
                data['chapters'] = chapters
                _save_json(os.path.join(DATA_DIR, f'{book}.json'), data)
                self._json({'ok': True, 'deleted': before - len(chapters)})
                return
            num = _norm(body.get('num'))
            if not num.isdigit():
                self._json({'error': 'Kapitelnummer muss eine Zahl sein'}, 400)
                return
            num = int(num)
            title = _norm(body.get('title')) or f'Kapitel {num}'
            words, errors = _parse_pairs(body.get('words') or '')
            if not words:
                self._json({'error': 'Keine gültigen Zeilen (Format: english;deutsch)'}, 400)
                return
            ch = {'num': num, 'title': title, 'words': words}
            data['chapters'] = [c for c in chapters if str(c.get('num')) != str(num)] + [ch]
            data['chapters'].sort(key=lambda c: int(c.get('num', 0)) if str(c.get('num', 0)).isdigit() else 999)
            _save_json(os.path.join(DATA_DIR, f'{book}.json'), data)
        self._json({'ok': True, 'num': num, 'count': len(words), 'errors': errors})


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Vokabelbuddy {VERSION} auf 0.0.0.0:{PORT}, Daten: {DATA_DIR}', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()