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
import base64
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import struct
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, urlunparse

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

# ----------------- AUTH (Passwort + TOTP, Planbrett-Muster) -----------------
AUTH_PATH = os.path.join(DATA_DIR, 'auth.json')
SESSION_COOKIE = 'vb_sess'
TRUST_COOKIE = 'vb_trust'
MFA_COOKIE = 'vb_mfa'
SESSION_TTL = 12 * 3600
TRUST_TTL = 30 * 86400
MFA_PENDING_TTL = 300
LOGIN_FAILS = {}  # ip -> [timestamps]
AUTH_MEM = {'users': {}, 'trusted': {}, 'trust_users': {}, 'sessions': {}, 'mfa_pending': {}}


def _b64(s):
    return base64.urlsafe_b64encode(s).decode().rstrip('=')


def _pbkdf2(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes(salt), 200_000).hex()


DEFAULT_USERS = {'luis': {'pw': 'luis', 'role': 'kid'}, 'carlotta': {'pw': 'carlotta', 'role': 'kid'}}


def _user_entry(pw, role):
    salt = list(os.urandom(16))
    return {'password_hash': _pbkdf2(pw, salt), 'salt': salt, 'role': role,
            'totp_secret': None, 'totp_enabled': False}


def auth_load():
    try:
        with open(AUTH_PATH, 'r') as f:
            d = json.load(f)
        AUTH_MEM['users'] = d.get('users') or {}
        AUTH_MEM['trusted'] = {str(k): v for k, v in (d.get('trusted') or {}).items()
                               if float(v) > time.time()}
        AUTH_MEM['trust_users'] = {str(k): str(v) for k, v in (d.get('trust_users') or {}).items()
                                   if str(k) in AUTH_MEM['trusted']}
    except Exception:
        AUTH_MEM.update({'users': {}, 'trusted': {}, 'trust_users': {}})


def auth_save():
    with _lock:
        d = {'users': AUTH_MEM['users'],
             'trusted': {k: v for k, v in AUTH_MEM['trusted'].items() if float(v) > time.time()},
             'trust_users': {k: AUTH_MEM.get('trust_users', {}).get(k, '')
                             for k in AUTH_MEM['trusted']}}
        tmp = AUTH_PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(d, f)
        os.replace(tmp, AUTH_PATH)
        try:
            os.chmod(AUTH_PATH, 0o600)
        except OSError:
            pass


def auth_has_password():
    # Setup fertig, sobald mind. EIN Admin-User existiert
    return any(u.get('role') == 'admin' for u in AUTH_MEM.get('users', {}).values())


def _session_user(self):
    """Eingeloggter User {id, role, kid} ODER None."""
    ck = self._cookies()
    sess = ck.get(SESSION_COOKIE)
    sessions = AUTH_MEM.setdefault('sessions', {})
    entry = sessions.get(sess) if sess else None
    if not entry or time.time() > entry.get('exp', 0):
        return None
    uid = entry.get('user')
    u = AUTH_MEM['users'].get(uid, {})
    return {'id': uid, 'role': u.get('role'), 'kid': uid if u.get('role') == 'kid' else None}


def _ensure_default_users():
    """luis/carlotta einmalig anlegen (Passwort = Username), nur wenn sie fehlen."""
    ch = False
    for uid, spec in DEFAULT_USERS.items():
        if uid not in AUTH_MEM['users']:
            AUTH_MEM['users'][uid] = _user_entry(spec['pw'], spec['role'])
            ch = True
    if ch:
        auth_save()


def _totp_now(secret, t=None):
    """RFC 6238 (8 digits, Planbrett-kompatibel)"""
    t = int(t if t is not None else time.time()) // 30
    key = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8), casefold=True)
    msg = struct.pack('>Q', t)
    dig = hmac.new(key, msg, hashlib.sha1).digest()
    o = dig[19] & 0x0f
    code = (struct.unpack('>I', dig[o:o + 4])[0] & 0x7fffffff) % 1_000_000_00
    return f'{code:08d}'


def totp_verify(secret, code):
    if not secret or not code:
        return False
    code = re.sub(r'[^0-9]', '', str(code))
    t = time.time()
    return any(_totp_now(secret, t + off) == code for off in (-30, 0, 30))


TRUST_USERS_FILE = 'trust_users'  # in AUTH_MEM (mit auth.json persistiert)


def _gc_trusted():
    now = time.time()
    trusted = AUTH_MEM.setdefault('trusted', {})
    tusers = AUTH_MEM.setdefault('trust_users', {})
    stale = [t for t, exp in trusted.items() if float(exp) < now]
    for t in stale:
        trusted.pop(t, None)
        tusers.pop(t, None)
    if stale:
        auth_save()


def _check_trust_token(tok):
    if not tok:
        return False
    exp = AUTH_MEM['trusted'].get(tok)
    if not exp or float(exp) < time.time():
        return False
    return True


def _new_token():
    return secrets.token_urlsafe(32)


def _set_cookie(name, value, max_age, path='/', secure=False):
    parts = [f'{name}={value}', 'Path=' + path, f'Max-Age={max_age}',
             'HttpOnly', 'SameSite=Lax']
    if secure:
        parts.append('Secure')
    return '; '.join(parts)


# ----------------- AUTH: Seiten + Gate -----------------
PUBLIC_GET = {'/api/health', '/login', '/mfa', '/setup', '/auth/state'}
PUBLIC_POST = {'/login', '/mfa', '/setup'}


class _AuthGateMixin:
    """Mixin in Handler: Cookies + Gate-Entscheidung."""

    def _cookies(self):
        raw = self.headers.get('Cookie') or ''
        out = {}
        for part in raw.split(';'):
            if '=' in part:
                k, v = part.split('=', 1)
                out[k.strip()] = v.strip()
        return out

    def _session_user(self):
        return _mod_session_user(self)

    def _is_authed(self):
        return self._session_user() is not None

    def _is_https(self):
        return (self.headers.get('X-Forwarded-Proto') or '').lower() == 'https' \
            or self.headers.get('X-Forwarded-Ssl') == 'on'

    def _auth_redirect(self, to):
        self.send_response(303)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _client_ip(self):
        # Hinter NPM/OpenResty: echte Client-IP aus X-Forwarded-For (erster Hop),
        # sonst Socket-IP (LAN-Direktzugriff).
        try:
            xff = self.headers.get('X-Forwarded-For')
            if xff:
                # Letzter Hop = vom eigenen Proxy hinzugefügt (nicht spoofbar); X-Real-IP setzt NPM.
                return (xff.split(',')[-1].strip() or self.headers.get('X-Real-IP') or '?')[:64]
            xr = self.headers.get('X-Real-IP')
            if xr:
                return xr.strip()[:64]
        except Exception:
            pass
        try:
            return self.client_address[0]
        except Exception:
            return '?'

    def _rate_limited(self, ip):
        now = time.time()
        fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 900]
        LOGIN_FAILS[ip] = fails
        return len(fails) >= 5

    def _record_fail(self, ip):
        LOGIN_FAILS.setdefault(ip, []).append(time.time())


    def _forced_kid(self, asked):
        """Eingeloggter User erzwingt das Kid: Kinder IMMER eigenes, Eltern NUR zugewiesene,
        Admin frei. None + 401/403-Antwort bei Konflikt."""
        me = self._session_user()
        if not me:
            self._json({'auth': True}, 401)
            return None
        if me['role'] == 'kid':
            if asked and asked != me['id']:
                self._json({'error': 'Nur dein eigener Lernstand zählt'}, 403)
                return None
            return me['id']
        if me['role'] == 'parent':
            allowed = AUTH_MEM['users'].get(me['id'], {}).get('kids') or []
            if asked and asked in allowed:
                return asked
            if asked:
                self._json({'error': 'Dieses Kind ist dir nicht zugewiesen'}, 403)
                return None
            return None
        # admin: freie Wahl
        if asked and _kid_ok(asked):
            return asked
        return None

    def _auth_gate(self, r):
        """False = bereits geantwortet (Login-Seite/401), True = weiter."""
        if not auth_has_password():
            return True  # Setup-Modus offen
        # Statische Branding-Assets IMMER frei (Login-Seite braucht sie!)
        if r.startswith('/assets/') or r.startswith('/favicon') or r in ('/manifest.json', '/sw.js'):
            return True
        pub = PUBLIC_GET if self.command in ('GET', 'HEAD') else PUBLIC_POST
        if r in pub or r == '/logout':
            return True  # Logout IMMER: muss Cookies/Trust auch bei abgelaufener Session löschen können
        if self._is_authed():
            return True
        if r.startswith('/api/'):
            self._json({'auth': True}, 401)
            return False
        self._auth_redirect('/login')
        return False


SECURITY_HEADERS = [
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'SAMEORIGIN'),
    ('Referrer-Policy', 'same-origin'),
    ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
    ('Content-Security-Policy',
     "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
     "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
     "connect-src 'self'; frame-ancestors 'self' https://homeassistant.henskes.cloud; "
     "base-uri 'self'; form-action 'self'"),
]


def _page(self, body, code=200, extra_headers=None):
    self.send_response(code)
    self.send_header('Content-Type', 'text/html; charset=utf-8')
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', 'no-store')
    for k, v in SECURITY_HEADERS:
        self.send_header(k, v)
    for k, v in (extra_headers or []):
        self.send_header(k, v)
    self.end_headers()
    if self.command != 'HEAD':
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.wfile.write(body)


_AUTH_CSS = ('body{font-family:Nunito,system-ui,sans-serif;background:#0B1B30;color:#EAF2FD;'
             'display:grid;place-items:center;min-height:100vh;margin:0}'
             '.card{background:#12305A;border-radius:16px;padding:26px;width:min(340px,90vw);'
             'box-shadow:0 12px 36px rgba(4,12,24,.45)}'
             '.brandline{display:flex;align-items:center;gap:10px;margin-bottom:16px}'
             '.brandline img.bl-logo{width:42px;height:42px;object-fit:contain}'
             '.brandline img.bl-mark{height:26px}'
             'h1{font-size:1.2rem;margin:0 0 14px}'
             'input{width:100%;box-sizing:border-box;padding:11px;border-radius:9px;'
             'border:1.5px solid #1E4478;background:#0B1B30;color:#EAF2FD;font-size:1rem;margin:6px 0}'
             'button{width:100%;padding:11px;border:0;border-radius:9px;background:#2E8BFF;'
             'color:#fff;font-weight:800;cursor:pointer;margin-top:8px}'
             '.msg{color:#F2A49E;font-size:.9rem;width:100%;text-align:center}'
             '.chk{display:flex;gap:8px;align-items:center;justify-content:center;'
             'font-size:.82rem;color:#8FA6C4;margin-top:2px;white-space:nowrap}'
             '.chk input{width:auto;margin:0;accent-color:#2E8BFF}'
             'form{width:100%}'
             '.bigbrand{display:flex;justify-content:center;margin:6px 0 20px}'
             '.biglogo{max-width:88%;width:auto;max-height:64px;object-fit:contain;filter:drop-shadow(0 6px 16px rgba(4,12,24,.5))}')
_AUTH_WRAP = ('<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width, initial-scale=1">'
              '<link rel="icon" type="image/png" sizes="32x32" href="/assets/mascot-32.png">'
              '<title>{t}</title><style>' + _AUTH_CSS + '</style></head><body>'
              '<div class="authcol"><div class="card">{b}</div></div></body></html>')


def _setup_html(msg=''):
    warn = f'<p class="msg">{msg}</p>' if msg else ''
    body = ('<h1>VokabelBuddy – Einrichtung</h1>'
            '<form method="POST" action="/setup">' + warn +
            '<input type="text" name="admin_user" placeholder="Eltern-Benutzer (z.B. tim)" autocapitalize="none" autocomplete="username">'
            '<input type="password" name="password" placeholder="Eltern-Kennwort (mind. 8 Zeichen)">'
            '<button>Kennwörter speichern & starten</button>'
            '<p class="hint" style="color:#8d94a8;font-size:.83rem">Für Luis und Carlotta werden '
            'automatisch Benutzer «luis» / «carlotta» mit entsprechendem Kennwort angelegt '
            '(du kannst sie später ändern).</p></form>')
    return _AUTH_WRAP.replace('{t}', 'Vokabelbuddy – Einrichtung').replace('{b}', body)


def _login_html(msg='', pre_user=''):
    warn = f'<p class="msg">{msg}</p>' if msg else ''
    pre = f' value="{pre_user}"' if pre_user else ''
    body = ('<div class="bigbrand">'
            '<img class="biglogo" src="/assets/logo-banner-white.png" alt="VokabelBuddy">'
            '</div>'
            '<form method="POST" action="/login">' + warn +
            f'<input type="text" name="username" placeholder="Benutzername (z.B. luis)"{pre} autocapitalize="none" autofocus>'
            '<input type="password" name="password" placeholder="Kennwort">'
            '<label class="chk"><input type="checkbox" name="trust" value="1" checked> '
            'Diesem Gerät 30 Tage vertrauen</label>'
            '<button>Anmelden</button></form>')
    return _AUTH_WRAP.replace('{t}', 'VokabelBuddy – Anmelden').replace('{b}', body)


def _mfa_html(msg=''):
    warn = f'<p class="msg">{msg}</p>' if msg else ''
    body = ('<h1>Prüfcode eingeben</h1>'
            '<form method="POST" action="/mfa">' + warn +
            '<input type="text" name="code" inputmode="numeric" pattern="[0-9]*" '
            'autocomplete="one-time-code" autofocus maxlength="6" '
            'style="font-size:1.2rem;letter-spacing:.35em;text-align:center">'
            '<button>Weiter</button></form>')
    return _AUTH_WRAP.replace('{t}', 'Vokabelbuddy – Code').replace('{b}', body)


def _mfa_setup_html(secret, msg=''):
    warn = f'<p class="msg">{msg}</p>' if msg else ''
    body = ('<h1>Zwei-Faktor einrichten</h1>'
            '<p style="color:#8d94a8;font-size:.86rem">Diesen Schlüssel in deiner '
            'Authenticator-App eintragen („Anderes Konto“ bzw. „+“):</p>'
            f'<p style="font-family:monospace;font-size:1.15rem;letter-spacing:.12em;'
            f'background:#141822;padding:10px;border-radius:9px;text-align:center">{secret}</p>'
            '<form method="POST" action="/mfa/confirm">' + warn +
            '<input type="text" name="code" inputmode="numeric" pattern="[0-9]*" '
            'maxlength="6" placeholder="6-stelliger Code aus der App" '
            'style="text-align:center;letter-spacing:.2em">'
            '<button>Bestätigen & aktivieren</button></form>')
    return _AUTH_WRAP.replace('{t}', 'Vokabelbuddy – MFA').replace('{b}', body)


def _gc_mfa_pending():
    now = time.time()
    pend = AUTH_MEM.get('mfa_pending', {})
    for k in [k for k, v in pend.items() if now - v.get('ts', 0) > MFA_PENDING_TTL]:
        pend.pop(k, None)


def _gc_sessions_auth():
    now = time.time()
    sessions = AUTH_MEM.get('sessions', {})
    for k in [k for k, v in sessions.items() if time.time() > v['exp']]:
        sessions.pop(k, None)


auth_load()


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
    if any(k['id'] == kid for k in KIDS):
        return True
    # Zusätzliche Kinder-Accounts (auth-users mit role kid) zählen als gültig
    u = AUTH_MEM.get('users', {}).get(kid or '')
    return bool(u) and u.get('role') == 'kid'


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



def _runs_rec():
    st = _stats()
    st.setdefault('runs', [])
    return st['runs']


def _runs_add(rec):
    with _lock:
        st = _stats()
        st.setdefault('runs', [])
        st['runs'].insert(0, rec)
        st['runs'] = st['runs'][:300]  # letzte 300 Runden
        _save_json(os.path.join(DATA_DIR, 'stats.json'), st)

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
            key = (en.casefold(), de.casefold())
            if key in seen:
                continue
            seen.add(key)
            gpos += 1
            if from_n and gpos < from_n:
                continue
            if to_n and gpos > to_n:
                continue
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


def _weighted_pick(pool, kid, count):
    """Gewichtete Auswahl (nie gesehen zuerst) — geteilt von MC-Quiz und Karteikarten."""
    st = _stats()
    seen_map = st['kid_seen'].get(kid, {})
    rng = random.Random()

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
    return [it for _w, _r, it in scored[:count]]


def _card_for(item, direction):
    """Karteikarte: front = Anzeige-Seite, back = Lösung (durch Umdrehen sichtbar)."""
    en, de = item['en'], item['de']
    if direction == 'de2en':
        return {'id': item['qid'], 'direction': 'de2en', 'chapter': item['num'],
                'pos': item['pos'], 'front': de, 'back': en}
    return {'id': item['qid'], 'direction': 'en2de', 'chapter': item['num'],
            'pos': item['pos'], 'front': en, 'back': de}


def _quiz_core(pool, dpool, kid, qtype, count, nonce):
    """Gemeinsame Quiz-Erzeugung: Gewichtung, Session, Fragen bauen."""
    if not pool:
        return None
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


def _make_quiz(book, kid, chapters, qtype, count, nonce, from_n=None, to_n=None):
    pool = _pool(book, chapters, from_n, to_n)
    if not pool:
        return None
    # Distraktor-Pool: die KAPITEL ungefiltert (auch bei Bereich 1–1 volle 4 Optionen)
    dpool = _pool(book, chapters)
    return _quiz_core(pool, dpool, kid, qtype, count, nonce)


def _make_mixed_session(book, kid, chapters, qtype, count, nonce, from_n=None, to_n=None, kinds_wanted=None):
    """Custom-Test: eine gewichtete Auswahl wird auf die GEWÄHLTEN Modi (mc/write/cards) verteilt.
    kinds_wanted = geordnete/geshuffelte Liste — Tim wählt aus, was abgefragt wird."""
    pool = _pool(book, chapters, from_n, to_n)
    if not pool:
        return None
    dpool = _pool(book, chapters)  # Distraktoren ungefiltert (wie _make_quiz)
    picked = _weighted_pick(pool, kid, count)
    rng = random.Random()
    dirs = (['en2de', 'de2en'] if qtype == 'both' else [qtype] if qtype in ('en2de', 'de2en')
            else ['en2de', 'de2en'])
    now = time.time()
    with _lock:
        _gc_sessions(now)
        _sessions[nonce] = {'created': now, 'questions': {}, 'written': {}}
    kinds = [k for k in (kinds_wanted or []) if k in ('mc', 'write', 'cards')]
    if not kinds:
        kinds = ['mc', 'write', 'cards']
    # gleichmäßig füllen: zyklisch + shuffeln (bei 12 und 2 Modi = 6/6):
    base = (kinds * 4)[:len(picked)]
    rng.shuffle(base)
    while len(base) < len(picked):
        base.append(rng.choice(kinds))
    kinds = base
    out = []
    for i, (item, kind) in enumerate(zip(picked, kinds)):
        d = dirs[i % len(dirs)]
        if kind == 'mc':
            q = _build_question(item, dpool, d, rng)
            with _lock:
                _sessions[nonce]['questions'][q['id'] + '|' + d] = q
            out.append({'kind': 'mc', 'id': q['id'], 'direction': d, 'chapter': item['num'],
                        'pos': item['pos'], 'prompt': q['prompt'], 'choices': q['choices']})
        else:
            if d == 'de2en':
                prompt, answer = item['de'], item['en']
            else:
                prompt, answer = item['en'], item['de']
            key = item['qid'] + '|' + d
            with _lock:
                if kind == 'write':
                    _sessions[nonce]['written'][key] = {'answer': answer, 'qid': item['qid'],
                                                        'direction': d, 'prompt': prompt}
            if kind == 'write':
                out.append({'kind': 'write', 'id': item['qid'], 'direction': d,
                            'chapter': item['num'], 'pos': item['pos'], 'prompt': prompt})
            else:
                card = _card_for(item, d)
                card['kind'] = 'cards'
                out.append(card)
    return out


def _make_wrong_quiz(book, kid, nonce):
    """Wiederholungs-Quiz: die in der letzten Session falsch beantworteten Vokabeln."""
    sess = _sessions.get(nonce)
    wrongs = list(sess.get('wrong', [])) if sess else []
    if not wrongs:
        return None
    by_ch = {}
    chapters = set()
    for w in wrongs:
        p = str(w.get('qid', '')).split('|')
        if len(p) < 3:
            continue
        chapters.add(p[1])
    if not chapters:
        return None
    pool_all = _pool(book, chapters)
    by_qid = {it['qid']: it for it in pool_all}
    pool = [by_qid[w['qid']] for w in wrongs if w.get('qid') in by_qid]
    if not pool:
        return None
    dpool = _pool(book, chapters)
    return _quiz_core(pool, dpool, kid, 'both', len(pool), nonce)


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


def _norm_text(s):
    """Text-Normalisierung für die Schreibprüfung: lowercase, Umlaut-Translit, Layout-Müll weg.
    '(to)'-Klammern und führendes 'to ' werden ignoriert (write == to write == (to) write)."""
    s = str(s or '').casefold().strip()
    s = s.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    s = re.sub(r'^\(to\)\s*', '', s)
    s = re.sub(r'^to\s+(?=[a-z])', '', s)  # 'to write' == 'write'
    s = re.sub(r'\((?:pl|no pl|AE|BE|infml|fml|usw)\)', '', s)
    s = re.sub(r'[.,;:!?…]+$', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def _levenshtein(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _alternatives(answer):
    """Übersetzung in acceptable Varianten splitten ('a, b / c; d').
    KLAMMER-ERGÄNZUNGEN werden abgespalten: 'vor der Schule (vor Schulbeginn)' → 'vor der Schule'."""
    parts = re.split(r'\s*[/;]\s*|\s*,\s*', str(answer or ''))
    variants = []
    for p in parts:
        # Klammern (+Inhalt) raus — sie sind optional-Info
        p = re.sub(r'\([^()]*\)', '', p)
        p = _norm_text(p)
        if p and p not in variants:
            variants.append(p)
    if not variants:
        n = _norm_text(answer)
        if n:
            variants = [n]
    return variants


DE_PLACEHOLDER = re.compile(r'^(?:sich|jn|jn\.|jm|jm\.|jemanden|jemandem|jemand|etwas|man|jdn|jd)\s+')


def _written_cmp(answer, text):
    """'no' | 'half' | 'full' — halb = Kernbedeutung stimmt (Platzhalter/Anteil ignoriert)."""
    t = _norm_text(text)
    if not t:
        return 'no'
    alts = _alternatives(answer)
    # 1) FULL: exakt oder 1-Tippfehler
    for alt in alts:
        if t == alt:
            return 'full'
        if len(alt) >= 5 and _levenshtein(t, alt) <= 1:
            return 'full'
        aw, tw = alt.split(), t.split()
        if len(aw) == len(tw) == 2 and all(
                a == b or (min(len(a), len(b)) >= 5 and _levenshtein(a, b) <= 1)
                for a, b in zip(aw, tw)):
            return 'full'
    # 2) HALF a): mehrteilige Lösung — eine Komponente exakt/lev1 passt
    if alts:
        for alt in alts:
            pass
        # Platzhalter strippen ('sich', 'jn.', 'jm.', 'etwas') — auf BEIDEN Seiten
        def strip_ph(s):
            prev = None
            s = ' ' + s + ' '
            while prev != s:
                prev = s
                s = re.sub(r'\s(?:sich|jn\.?|jm\.?|jemanden|jemandem|jemand|etwas|man|jdn\.?|jd\.?)(\s)', r'\1', s)
            return s.strip()
        t2 = strip_ph(t)
        for alt in alts:
            a2 = strip_ph(alt)
            # Komponente der Lösung exakt getippt (nach Platzhalter-Strip)
            if a2 == t2 and a2:
                return 'half'
            if len(a2) >= 5 and a2 == t2:
                return 'half'
            if len(a2) >= 6 and _levenshtein(t2, a2) <= 1:
                return 'half'
    # 3) HALF c): nah dran (2 Tippfehler) bei langen Wörtern
    for alt in alts:
        if len(alt) >= 8 and _levenshtein(t, alt) <= 2:
            return 'half'
    return 'no'


def _written_ok(answer, text):
    return _written_cmp(answer, text) == 'full'


def _make_write_session(book, kid, chapters, qtype, count, nonce, from_n=None, to_n=None):
    """Schreib-Session: Items mit Lösung serverseitig, Frontend bekommt nur prompt+id."""
    pool = _pool(book, chapters, from_n, to_n)
    if not pool:
        return None
    picked = _weighted_pick(pool, kid, count)
    rng = random.Random()
    dirs = (['en2de', 'de2en'] if qtype == 'both'
            else [qtype] if qtype in ('en2de', 'de2en')
            else ['en2de', 'de2en'])
    now = time.time()
    with _lock:
        _gc_sessions(now)
        _sessions[nonce] = {'created': now, 'questions': {}, 'written': {}}
    items = []
    for i, it in enumerate(picked):
        d = dirs[i % len(dirs)]
        if d == 'de2en':
            prompt, answer = it['de'], it['en']
        else:
            prompt, answer = it['en'], it['de']
        key = it['qid'] + '|' + d
        with _lock:
            _sessions[nonce]['written'][key] = {'answer': answer, 'qid': it['qid'],
                                                'direction': d, 'prompt': prompt}
        items.append({'id': it['qid'], 'direction': d, 'chapter': it['num'],
                      'pos': it['pos'], 'prompt': prompt})
    return items


_mod_session_user = _session_user  # Modulfunktion für das Mixin


class Handler(_AuthGateMixin, BaseHTTPRequestHandler):
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
        # PWA: Manifest, SW, Assets (weißliste, sandboxed auf ROOT)
        m = {'/manifest.json': 'application/manifest+json', '/sw.js': 'application/javascript',
             '/favicon.ico': 'image/x-icon', '/favicon-32.png': 'image/png', '/favicon-16.png': 'image/png'}
        a = {'.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}
        if path in m:
            base = os.path.basename(path)
            p = os.path.join(ROOT, base)
            if not os.path.exists(p):
                p = os.path.join(ROOT, 'assets', base)  # favicons liegen im assets/
            if os.path.exists(p):
                self._send(200, open(p, 'rb').read(), m[path])
                return True
            self._send(404, b'{}')
            return True
        if path.startswith('/assets/'):
            base = os.path.basename(path)  # basename! kein Pfad-Traversal
            p = os.path.join(ROOT, 'assets', base)
            ext = os.path.splitext(base)[1].lower()
            if os.path.exists(p) and ext in a:
                self._send(200, open(p, 'rb').read(), a[ext])
                return True
            self._send(404, b'{}')
            return True
        return False

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        r = u.path
        try:
            # ---- AUTH-GATE ----
            if r == '/setup' and not auth_has_password():
                _page(self, _setup_html())
                return
            if r == '/auth/state':
                if auth_has_password() is False:
                    self._json({'auth': False, 'setup': True})
                    return
                me = self._session_user()
                if me:
                    u = AUTH_MEM['users'].get(me['id'], {})
                    self._json({'auth': True, 'user': me['id'], 'role': me['role'],
                                'kid': me['kid'], 'mfa': bool(u.get('totp_enabled')),
                                'kids': (u.get('kids') or []) if me['role']=='parent' else
                                        ([k['id'] for k in KIDS] if me['role']=='admin' else [])})
                else:
                    self._json({'auth': True}, 401)
                return
            if not self._auth_gate(r):
                return
            if auth_has_password() is False:
                # Setup-Modus: App nur als Hinweisseite
                self._auth_redirect('/setup')
                return
            if r == '/api/health':
                self._json({'ok': True, 'version': VERSION})
            elif r == '/api/meta':
                extra_kids = [{'id': uid, 'name': uid.capitalize(), 'color': '#a78bfa'}
                              for uid, u in AUTH_MEM.get('users', {}).items()
                              if u.get('role') == 'kid' and not any(k['id'] == uid for k in KIDS)]
                self._json({'version': VERSION, 'books': BOOKS, 'kids': KIDS + extra_kids,
                            'default_book': DEFAULT_BOOKS})
            elif r == '/api/chapters':
                self.api_chapters(q)
            elif r == '/api/quiz':
                self.api_quiz(q)
            elif r == '/api/write':
                self.api_write(q)
            elif r == '/api/cards':
                self.api_cards(q)
            elif r == '/api/answer':
                self.api_answer(q, {})
            elif r == '/api/stats':
                self.api_stats(q)
            elif r == '/api/report':
                self.api_report(q)
            elif r == '/api/auth/parents':
                self.api_auth_parents(q)
            elif r == '/api/history':
                self.api_history(q)
            elif r == '/api/export':
                self.api_export(q)
            elif r == '/api/words':
                self.api_words(q)
            elif r == '/login':
                if self._is_authed():
                    self._auth_redirect('/')
                    return
                # „30 Tage vertrauen“: gültiges Trust-Cookie erstellt eine neue Session ohne PW
                # — ABER NUR bei Usern OHNE MFA (sonst würde Trust den 2. Faktor umgehen!)
                tk = self._cookies().get(TRUST_COOKIE)
                if tk and _check_trust_token(tk):
                    uid = (AUTH_MEM.get('trust_users') or {}).get(tk)
                    uu = AUTH_MEM['users'].get(uid) if uid else None
                    if uu and not uu.get('totp_enabled'):
                        sess = _new_token()
                        with _lock:
                            AUTH_MEM.setdefault('sessions', {})[sess] = {'exp': time.time() + SESSION_TTL, 'user': uid}
                        self.send_response(303)
                        self.send_header('Location', '/')
                        self.send_header('Set-Cookie', _set_cookie(SESSION_COOKIE, sess, SESSION_TTL, secure=self._is_https()))
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                _page(self, _login_html())
            elif r == '/mfa':
                ck = self._cookies()
                pend = AUTH_MEM.get('mfa_pending', {})
                tok = ck.get(MFA_COOKIE)
                if not tok or tok not in pend or time.time() - pend[tok]['ts'] > MFA_PENDING_TTL:
                    self._auth_redirect('/login')
                    return
                _page(self, _mfa_html())
            elif r == '/mfa/setup':
                me = self._session_user()
                if not me:
                    self._auth_redirect('/login')
                    return
                u = AUTH_MEM['users'].setdefault(me['id'], {})
                if u.get('totp_enabled'):
                    _page(self, _AUTH_WRAP.replace('{t}', 'MFA').replace('{b}', '<h1>Zwei-Faktor ist aktiv</h1>'))
                    return
                old_secret = u.get('totp_secret')
                def _valid_b32(s):
                    return bool(s) and re.fullmatch(r'[A-Z2-7]+', s or '') and len(s) >= 16 and (len(s) % 8) not in (1, 3, 6)
                if not _valid_b32(old_secret):
                    secret = base64.b32encode(os.urandom(10)).decode().rstrip('=')
                else:
                    secret = old_secret
                u['totp_secret'] = secret
                u['totp_enabled'] = False
                auth_save()
                _page(self, _mfa_setup_html(secret))
            elif r == '/logout':
                ck = self._cookies()
                sess = ck.get(SESSION_COOKIE)
                with _lock:
                    if sess:
                        AUTH_MEM.setdefault('sessions', {}).pop(sess, None)
                    # Trust-Cookie serverseitig INVALIDIEREN (nicht nur Browser-Cookie löschen):
                    trust = ck.get(TRUST_COOKIE)
                    if trust:
                        AUTH_MEM.setdefault('trusted', {}).pop(trust, None)
                        auth_save()
                headers = [('Set-Cookie', _set_cookie(SESSION_COOKIE, '', 0, secure=self._is_https())),
                           ('Set-Cookie', _set_cookie(TRUST_COOKIE, '', 0, secure=self._is_https())),
                           ('Refresh', '3; url=/login')]
                _page(self, _AUTH_WRAP.replace('{t}', 'Abgemeldet').replace('{b}',
                      '<div class="bigbrand"><img class="biglogo" src="/assets/logo-banner-white.png" alt="VokabelBuddy"></div>'
                      '<h1>Abgemeldet</h1>'
                      '<p style="color:#8FA6C4; text-align:center">Du wirst zur Anmeldeseite weitergeleitet…</p>'
                      '<p style="text-align:center; margin-top:14px"><a href="/login" style="color:#2E8BFF; font-weight:700">Sofort weiter</a></p>'),
                      extra_headers=headers)
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

    def api_mfa_confirm(self, body):
        ck = self._cookies()
        pend_tok = ck.get(MFA_COOKIE) if False else None  # confirm läuft im SETZEN-Kontext (session)
        me = self._session_user()
        code = _norm(body.get('code'))
        # Setup-Kontext: user, dessen Secret gerade PENDING ist (totp_enabled False + Secret frisch)
        cand = None
        if me:
            cand = me['id']
        else:
            # nach Login ohne MFA: session existiert bereits (me da) — sonst abbrechen
            pass
        if not cand:
            self._auth_redirect('/login')
            return
        u = AUTH_MEM['users'].get(cand, {})
        secret = u.get('totp_secret')
        if not secret or not totp_verify(secret, code):
            _page(self, _mfa_setup_html(secret or '?', 'Code falsch – nochmal versuchen'))
            return
        u['totp_enabled'] = True
        auth_save()
        _page(self, _AUTH_WRAP.replace('{t}', 'MFA aktiv').replace('{b}', '<h1>✅ Zwei-Faktor ist aktiv</h1>'
               '<p style="color:#8d94a8">Ab dem nächsten Login zusätzlich zum Kennwort nötig.</p>'))

    def do_POST(self):
        u = urlparse(self.path)
        r = u.path
        try:
            ip = self._client_ip()
            if self._rate_limited(ip) and r not in ('/logout', '/login'):
                # API-POSTs während eines Login-Bans NICHT blocken (App bleibt bedienbar);
                # nur nicht-/login-SeitenPOSTs bekommen die Hinweisseite.
                if r.startswith('/api/'):
                    pass  # API geht durch — Gate prüft Auth normal
                else:
                    _page(self, _login_html('Zu viele Versuche – bitte 15 Minuten warten.'))
                    return
            body = self._body()
            if r == '/setup':
                if auth_has_password():
                    self._auth_redirect('/login')
                    return
                auser = _norm(body.get('admin_user')).lower()
                pw1 = _norm(body.get('password'))
                if not re.fullmatch(r'[a-z0-9_]{3,20}', auser or ''):
                    _page(self, _setup_html('Benutzername: 3–20 Zeichen, a–z/0–9/_'))
                    return
                if auser in ('luis', 'carlotta'):
                    _page(self, _setup_html('Diesen Namen nutzt bereits ein Kind – wähle einen anderen.'))
                    return
                if len(pw1) < 8:
                    _page(self, _setup_html('Kennwort zu kurz – mindestens 8 Zeichen.'))
                    return
                with _lock:
                    _ensure_default_users()
                    AUTH_MEM['users'][auser] = _user_entry(pw1, 'admin')
                    auth_save()
                sess = _new_token()
                AUTH_MEM.setdefault('sessions', {})[sess] = {'exp': time.time() + SESSION_TTL, 'user': auser}
                trust = _new_token()
                AUTH_MEM.setdefault('trusted', {})[trust] = time.time() + TRUST_TTL
                auth_save()
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie', _set_cookie(SESSION_COOKIE, sess, SESSION_TTL))
                self.send_header('Set-Cookie', _set_cookie(TRUST_COOKIE, trust, TRUST_TTL))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
                salt = list(os.urandom(16))
                AUTH_MEM['password_hash'] = _pbkdf2(pw1, salt)
                AUTH_MEM['salt'] = salt
                auth_save()
                # direkt Session ausstellen:
                tok = _new_token()
                AUTH_MEM.setdefault('sessions', {})[tok] = {'exp': time.time() + SESSION_TTL}
                trust = _new_token()
                AUTH_MEM.setdefault('trusted', {})[trust] = time.time() + TRUST_TTL
                auth_save()
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie', _set_cookie(SESSION_COOKIE, tok, SESSION_TTL, secure=self._is_https()))
                self.send_header('Set-Cookie', _set_cookie(TRUST_COOKIE, trust, TRUST_TTL, secure=self._is_https()))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if r == '/login':
                if not auth_has_password():
                    self._auth_redirect('/setup')
                    return
                if self._rate_limited(ip):
                    _page(self, _login_html('Zu viele Versuche – bitte 15 Minuten warten.'))
                    return
                auser = _norm(body.get('username')).lower()
                pw = _norm(body.get('password'))
                u = AUTH_MEM['users'].get(auser)
                okpw = bool(u) and hmac.compare_digest(
                    str(u.get('password_hash') or ''), _pbkdf2(pw, u.get('salt') or [0]*16))
                if not okpw:
                    self._record_fail(ip)
                    _page(self, _login_html('Benutzername oder Kennwort falsch.', pre_user=auser))
                    return
                LOGIN_FAILS[ip] = []
                if u.get('totp_enabled'):
                    pend_tok = _new_token()
                    AUTH_MEM.setdefault('mfa_pending', {})[pend_tok] = {
                        'ts': time.time(), 'user': auser,
                        'trust': bool(_norm(body.get('trust')))}
                    _gc_mfa_pending()
                    self.send_response(303)
                    self.send_header('Location', '/mfa')
                    self.send_header('Set-Cookie', _set_cookie(MFA_COOKIE, pend_tok, MFA_PENDING_TTL))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                sess = _new_token()
                AUTH_MEM.setdefault('sessions', {})[sess] = {'exp': time.time() + SESSION_TTL, 'user': auser}
                _gc_sessions_auth()
                headers = [('Set-Cookie', _set_cookie(SESSION_COOKIE, sess, SESSION_TTL, secure=self._is_https()))]
                if _norm(body.get('trust')):
                    trust = _new_token()
                    with _lock:
                        AUTH_MEM.setdefault('trusted', {})[trust] = time.time() + TRUST_TTL
                        AUTH_MEM.setdefault('trust_users', {})[trust] = auser
                        auth_save()
                    headers.append(('Set-Cookie', _set_cookie(TRUST_COOKIE, trust, TRUST_TTL, secure=self._is_https())))
                self.send_response(303)
                self.send_header('Location', '/')
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if r == '/mfa':
                ck = self._cookies()
                tok = ck.get(MFA_COOKIE)
                pend = AUTH_MEM.get('mfa_pending', {})
                entry = pend.get(tok) if tok else None
                if not entry or time.time() - entry['ts'] > MFA_PENDING_TTL:
                    _page(self, _login_html('Sitzung abgelaufen – bitte erneut anmelden.'))
                    return
                auser = entry.get('user')
                u = AUTH_MEM['users'].get(auser, {})
                if not totp_verify(u.get('totp_secret'), _norm(body.get('code'))):
                    self._record_fail(ip)
                    _page(self, _mfa_html('Code falsch – nochmal versuchen.'))
                    return
                pend.pop(tok, None)
                sess = _new_token()
                AUTH_MEM.setdefault('sessions', {})[sess] = {'exp': time.time() + SESSION_TTL, 'user': auser}
                _gc_sessions_auth()
                headers = [('Set-Cookie', _set_cookie(SESSION_COOKIE, sess, SESSION_TTL, secure=self._is_https())),
                           ('Set-Cookie', _set_cookie(MFA_COOKIE, '', 0))]
                if entry.get('trust'):
                    trust = _new_token()
                    with _lock:
                        AUTH_MEM.setdefault('trusted', {})[trust] = time.time() + TRUST_TTL
                        AUTH_MEM.setdefault('trust_users', {})[trust] = auser
                        auth_save()
                    headers.append(('Set-Cookie', _set_cookie(TRUST_COOKIE, trust, TRUST_TTL, secure=self._is_https())))
                self.send_response(303)
                self.send_header('Location', '/')
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if r == '/mfa/confirm':
                self.api_mfa_confirm(body)
                return
            if r == '/mfa/confirm-json':
                me = self._session_user()
                if not me:
                    self._json({'error': 'nicht angemeldet'}, 401)
                    return
                u = AUTH_MEM['users'].setdefault(me['id'], {})
                secret = u.get('totp_secret')
                if not secret or not totp_verify(secret, _norm(body.get('code'))):
                    self._json({'error': 'Code falsch'}, 400)
                    return
                u['totp_enabled'] = True
                auth_save()
                self._json({'ok': True, 'mfa': True})
                return
            if r == '/api/run':
                # Rundenende melden {kid, book, chapters, mode, n, score, wrong:[{prompt,answer}]} — kid-Zwang
                kid = _norm(body.get('kid'))
                fkid = self._forced_kid(kid) if auth_has_password() else kid
                if auth_has_password() and fkid is None:
                    self._json({'error': 'Parameter fehlen'}, 400)
                    return
                kid = fkid
                if not _kid_ok(kid):
                    self._json({'error': 'kid unbekannt'}, 400)
                    return
                rec = {'kid': kid,
                       'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                       'mode': _norm(body.get('mode')) or 'mc',
                       'book': _norm(body.get('book')),
                       'chapters': _norm(body.get('chapters')),
                       'n': int(body.get('n') or 0),
                       'score': float(body.get('score') or 0),
                       'wrong': (body.get('wrong') or [])[:40],
                       'detail': (body.get('detail') or [])[:40]}
                _runs_add(rec)
                self._json({'ok': True})
                return
            if r == '/api/parent/kids':
                me = self._session_user()
                if not me or me.get('role') != 'admin':
                    self._json({'error': 'Nur der Admin kann zuweisen'}, 403)
                    return
                puser = _norm(body.get('user'))
                kids = body.get('kids') or []
                if puser not in AUTH_MEM['users'] or AUTH_MEM['users'][puser].get('role') != 'parent':
                    self._json({'error': 'Eltern-Account unbekannt'}, 400)
                    return
                kids = [k for k in kids if _kid_ok(k)]
                AUTH_MEM['users'][puser]['kids'] = kids
                auth_save()
                self._json({'ok': True, 'user': puser, 'kids': kids})
                return
            if r == '/api/account/reset':
                me = self._session_user()
                if not me or me.get('role') != 'admin':
                    self._json({'error': 'Nur der Admin darf Kennwörter zurücksetzen'}, 403)
                    return
                tgt = _norm(body.get('user')).lower()
                pw = _norm(body.get('password'))
                u = AUTH_MEM['users'].get(tgt)
                if not u:
                    self._json({'error': 'Benutzer unbekannt'}, 400)
                    return
                if len(pw) < 4:
                    self._json({'error': 'Kennwort: mindestens 4 Zeichen'}, 400)
                    return
                u['salt'] = list(os.urandom(16))
                u['password_hash'] = _pbkdf2(pw, u['salt'])
                # alle Sessions + Trust des Users killen (Sicherheit):
                for k, v in list(AUTH_MEM.get('sessions', {}).items()):
                    if v.get('user') == tgt:
                        AUTH_MEM['sessions'].pop(k, None)
                for k, v in list(AUTH_MEM.get('trust_users', {}).items()):
                    if v == tgt:
                        AUTH_MEM['trust_users'].pop(k, None)
                        AUTH_MEM['trusted'].pop(k, None)
                auth_save()
                self._json({'ok': True, 'user': tgt})
                return
            if r == '/api/child/create':
                me = self._session_user()
                if not me or me.get('role') != 'admin':
                    self._json({'error': 'Nur der Admin kann Kinder anlegen'}, 403)
                    return
                cuser = _norm(body.get('user')).lower()
                pw = _norm(body.get('password'))
                book = _norm(body.get('book'))
                if not re.fullmatch(r'[a-z0-9_]{3,20}', cuser or ''):
                    self._json({'error': 'Kinder-Benutzername: 3–20 Zeichen (a–z 0–9 _)'}, 400)
                    return
                if len(pw) < 4:
                    self._json({'error': 'Kennwort: mindestens 4 Zeichen'}, 400)
                    return
                if cuser in AUTH_MEM['users']:
                    self._json({'error': 'Diesen Benutzer gibt es schon'}, 400)
                    return
                if book and book not in _book_ids():
                    book = ''
                salt = list(os.urandom(16))
                AUTH_MEM['users'][cuser] = {'password_hash': _pbkdf2(pw, salt), 'salt': salt,
                                            'role': 'kid', 'book': book,
                                            'totp_secret': None, 'totp_enabled': False}
                if book: DEFAULT_BOOKS[cuser] = book
                # neue Kids in das Frontend-KIDS-Array (Basis-Anzeige, Farb-Auto):
                auth_save()
                self._json({'ok': True, 'user': cuser, 'book': book})
                return
            if r == '/api/child/delete':
                me = self._session_user()
                if not me or me.get('role') != 'admin':
                    self._json({'error': 'Nur der Admin'}, 403)
                    return
                cuser = _norm(body.get('user'))
                if cuser in (k['id'] for k in KIDS):
                    self._json({'error': 'Die Stammkinder (luis/carlotta) bleiben'}, 400)
                    return
                if cuser not in AUTH_MEM['users'] or AUTH_MEM['users'][cuser].get('role') != 'kid':
                    self._json({'error': 'Kinder-Account unbekannt'}, 400)
                    return
                # erst aus Eltern-Zuweisungen raus:
                for u in AUTH_MEM['users'].values():
                    if isinstance(u.get('kids'), list) and cuser in u['kids']:
                        u['kids'] = [x for x in u['kids'] if x != cuser]
                AUTH_MEM['users'].pop(cuser, None)
                auth_save()
                self._json({'ok': True})
                return
            if r == '/api/parent/create':
                me = self._session_user()
                if not me or me.get('role') != 'admin':
                    self._json({'error': 'Nur der Admin kann Accounts anlegen'}, 403)
                    return
                puser = _norm(body.get('user')).lower()
                pw = _norm(body.get('password'))
                kids = [k for k in (body.get('kids') or []) if _kid_ok(k)]
                if not re.fullmatch(r'[a-z0-9_]{3,20}', puser or ''):
                    self._json({'error': 'Eltern-Benutzername: 3–20 Zeichen (a–z 0–9 _)'}, 400)
                    return
                if len(pw) < 4:
                    self._json({'error': 'Kennwort: mindestens 4 Zeichen'}, 400)
                    return
                if puser in AUTH_MEM['users']:
                    self._json({'error': 'Diesen Benutzer gibt es schon'}, 400)
                    return
                salt = list(os.urandom(16))
                AUTH_MEM['users'][puser] = {'password_hash': _pbkdf2(pw, salt), 'salt': salt,
                                            'role': 'parent', 'kids': kids,
                                            'totp_secret': None, 'totp_enabled': False}
                auth_save()
                self._json({'ok': True, 'user': puser, 'kids': kids})
                return
            if r == '/api/account/password':
                me = self._session_user()
                if not me:
                    self._json({'error': 'nicht angemeldet'}, 401)
                    return
                cur = _norm(body.get('current'))
                n1 = _norm(body.get('new'))
                n2 = _norm(body.get('new2'))
                u = AUTH_MEM['users'].get(me['id'], {})
                if not hmac.compare_digest(str(u.get('password_hash') or ''),
                                           _pbkdf2(cur, u.get('salt') or [0]*16)):
                    self._json({'error': 'Aktuelles Kennwort ist falsch'}, 403)
                    return
                if len(n1) < 4:
                    self._json({'error': 'Neues Kennwort: mindestens 4 Zeichen'}, 400)
                    return
                if n1 != n2:
                    self._json({'error': 'Neue Kennwörter stimmen nicht überein'}, 400)
                    return
                u['salt'] = list(os.urandom(16))
                u['password_hash'] = _pbkdf2(n1, u['salt'])
                auth_save()
                self._json({'ok': True})
                return
            if r == '/api/account/status':
                me = self._session_user()
                if not me:
                    self._json({'auth': True}, 401)
                    return
                u = AUTH_MEM['users'].get(me['id'], {})
                # secret nur zeigen, wenn MFA im EINRICHTUNGS-Zustand (aktiv aber nicht bestätigt)
                reveal = bool(u.get('totp_secret')) and not bool(u.get('totp_enabled'))
                self._json({'user': me['id'], 'mfa': bool(u.get('totp_enabled')),
                            'secret': u.get('totp_secret') if reveal else None})
                return
            if r == '/mfa/start':
                me = self._session_user()
                if not me:
                    self._json({'error': 'nicht angemeldet'}, 401)
                    return
                u = AUTH_MEM['users'].setdefault(me['id'], {})
                secret = base64.b32encode(os.urandom(10)).decode().rstrip('=')
                u['totp_secret'] = secret
                u['totp_enabled'] = False
                auth_save()
                self._json({'ok': True, 'secret': secret})
                return
            if r == '/mfa/disable':
                me = self._session_user()
                if not me:
                    self._json({'error': 'nicht angemeldet'}, 401)
                    return
                u = AUTH_MEM['users'].setdefault(me['id'], {})
                u['totp_enabled'] = False
                u['totp_secret'] = None
                auth_save()
                self._json({'ok': True, 'mfa': False})
                return
            # ---- AUTH-GATE für alle anderen POST ----
            if not self._auth_gate(r):
                return
            if auth_has_password() is False:
                self._json({'auth': True}, 401)
                return
            if r == '/api/answer':
                self.api_answer(parse_qs(u.query), body)
            elif r == '/api/quiz/wrong':
                self.api_quiz_wrong(body)
            elif r == '/api/card/answer':
                self.api_card_answer(body)
            elif r == '/api/write/check':
                self.api_write_check(body)
            elif r == '/api/chapter/save':
                self.api_chapter_save(body)
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
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
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
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
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
        if qtype == 'mixed':
            # Gewählte Modi (kommagetrennt: mc,write,cards) — Tim wählt aus, was gefragt wird:
            raw_kinds = [k.strip() for k in ((q.get('modes') or [''])[0].split(',')) if k.strip()]
            items = _make_mixed_session(book, kid, chapters, qtype, (q.get('count') or ['12'])[0], nonce, from_n, to_n, raw_kinds)
            if items is None:
                self._json({'error': 'Keine Vokabeln in diesem Bereich'}, 404)
                return
            self._json({'book': book, 'kid': kid, 'kind': 'mixed', 'questions': items})
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
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
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
        if not correct and sess is not None:
            entry = {'qid': qid, 'prompt': question['prompt'], 'answer': question['answer'],
                     'direction': direction}
            wrongs = sess.setdefault('wrong', [])
            if not any(w.get('qid') == qid for w in wrongs):
                wrongs.append(entry)
        _record(kid, qid, correct)
        self._json({'correct': correct, 'answer': question['answer'], 'ok': True})

    def api_write(self, q):
        """Schreib-Session starten (gleiche Parameter wie /api/quiz, GET)."""
        book = (q.get('book') or [''])[0]
        kid = (q.get('kid') or [''])[0]
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
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
        if book not in _book_ids() or not _kid_ok(kid) or not (6 <= len(nonce) <= 64) or not chapters:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        items = _make_write_session(book, kid, chapters, qtype, (q.get('count') or ['12'])[0], nonce, from_n, to_n)
        if items is None:
            self._json({'error': 'Keine Vokabeln in diesem Bereich'}, 404)
            return
        self._json({'book': book, 'kid': kid, 'mode': 'write', 'items': items})

    def api_write_check(self, body):
        """Antwort im Schreibmodus prüfen {kid,nonce,key,text}."""
        kid = _norm(body.get('kid'))
        nonce = _norm(body.get('nonce'))
        key = _norm(body.get('key'))
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        text = _norm(body.get('text'))
        if not _kid_ok(kid) or not (6 <= len(nonce) <= 64) or not key:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        sess = _sessions.get(nonce)
        entry = sess.get('written', {}).get(key) if sess else None
        if entry is None:
            self._json({'error': 'Schreib-Sitzung abgelaufen – bitte neu starten'}, 404)
            return
        correct = _written_ok(entry['answer'], text)
        verdict = _written_cmp(entry['answer'], text)
        half = verdict == 'half'
        # Groß-/Kleinschreibung-Hinweis: korrekt, aber Schreibung abweichend
        caps_note = ''
        if correct:
            t_naked = re.sub(r'\s+', ' ', str(text or '')).strip()
            ref = entry['answer']
            # exakt-gleich (case-sensitiv, ohne Rauschzeichen)?
            def _naked(s):
                return re.sub(r'\s+', ' ', str(s or '')).strip().rstrip('.,;:!?…')
            if _naked(t_naked) != _naked(ref):
                caps_note = ref
        if not correct:
            wrongs = sess.setdefault('wrong', [])
            if not any(w.get('qid') == entry['qid'] for w in wrongs):
                wrongs.append({'qid': entry['qid'], 'prompt': entry.get('prompt', ''),
                               'answer': entry['answer'], 'direction': entry['direction'],
                               'half': half})
        # halbe Punkte: halb = Kernbedeutung — wird für die Statistik wie richtig gezählt
        # (mit 'half'-Kennzeichen), im Frontend aber als „Fast richtig (½)“ angezeigt
        if half and not correct:
            # halbe Punkte = halbe richtige Reaktion: wir buchen n+1 und s+0.5 →
            # _record nutzt ints; wir merken Halbpunkte als Extra-Stat 'half'
            with _lock:
                st = _stats()
                e = st['kid_seen'].setdefault(kid, {}).setdefault(entry['qid'], {'n': 0, 's': 0, 'streak': 0})
                e['half'] = int(e.get('half', 0) or 0) + 1
                e['last'] = datetime.now().strftime('%Y-%m-%d')
                _save_json(os.path.join(DATA_DIR, 'stats.json'), st)
        _record(kid, entry['qid'], correct or half)
        self._json({'correct': correct, 'half': half, 'answer': entry['answer'],
                    'caps_note': caps_note, 'ok': True})

    def api_cards(self, q):
        """Karteikarten-Stapel (gleiche Auswahl-Parameter wie /api/quiz)."""
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
        if book not in _book_ids() or not _kid_ok(kid) or not (6 <= len(nonce) <= 64) or not chapters:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        pool = _pool(book, chapters, from_n, to_n)
        if not pool:
            self._json({'error': 'Keine Vokabeln in diesem Bereich'}, 404)
            return
        picked = _weighted_pick(pool, kid, (q.get('count') or ['12'])[0])
        rng = random.Random()
        dirs = (['en2de', 'de2en'] if qtype == 'both'
                else [qtype] if qtype in ('en2de', 'de2en')
                else ['en2de', 'de2en'])
        now = time.time()
        with _lock:
            _gc_sessions(now)
            _sessions[nonce] = {'created': now, 'questions': {}}
        cards = []
        for i, item in enumerate(picked):
            d = dirs[i % len(dirs)]
            card = _card_for(item, d)
            cards.append(card)
        self._json({'book': book, 'kid': kid, 'mode': 'cards', 'kind': qtype, 'cards': cards})

    def api_card_answer(self, body):
        """Selbstbewertung einer Karteikarte ('knew' | 'forgot')."""
        kid = _norm(body.get('kid'))
        card_id = _norm(body.get('qid'))
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        verdict = _norm(body.get('verdict'))
        direction = _norm(body.get('direction')) or 'en2de'
        nonce = _norm(body.get('nonce'))
        if not _kid_ok(kid) or not card_id or not (6 <= len(nonce) <= 64):
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        correct = verdict == 'knew'
        _record(kid, card_id, correct)
        if not correct:
            sess = _sessions.get(nonce)
            if sess is not None:
                wrongs = sess.setdefault('wrong', [])
                if not any(w.get('qid') == card_id for w in wrongs):
                    p = card_id.split('|')
                    wrongs.append({'qid': card_id, 'prompt': p[2] if len(p) > 2 else card_id,
                                   'answer': '', 'direction': direction})
        self._json({'ok': True})

    def api_quiz_wrong(self, body):
        book = _norm(body.get('book'))
        kid = _norm(body.get('kid'))
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        nonce = _norm(body.get('nonce'))
        if book not in _book_ids() or not _kid_ok(kid) or not (6 <= len(nonce) <= 64):
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        qs = _make_wrong_quiz(book, kid, nonce)
        if qs is None:
            self._json({'error': 'Keine falschen Vokabeln in dieser Runde'}, 404)
            return
        self._json({'book': book, 'kid': kid, 'kind': 'wrong', 'questions': qs})

    def api_stats(self, q):
        kid = (q.get('kid') or [''])[0]
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        st = _stats()
        if kid:
            if not _kid_ok(kid):
                self._json({'error': 'kid unbekannt'}, 400)
                return
            self._json({'kid': kid, 'chapters': _chapter_stats(st, kid)})
            return
        self._json({'by_kid': {k['id']: _chapter_stats(st, k['id']) for k in KIDS}})

    def api_auth_parents(self, q):
        me = self._session_user() if auth_has_password() else {'role':'admin'}
        if not me or me.get('role') != 'admin':
            self._json({'error': 'Nur der Admin'}, 403)
            return
        out = [{'user': uid, 'kids': (u.get('kids') or [])}
               for uid, u in AUTH_MEM['users'].items() if u.get('role') == 'parent']
        kids_out = [{'user': uid, 'book': (u.get('book') or '')}
                    for uid, u in AUTH_MEM['users'].items()
                    if u.get('role') == 'kid' and not any(k['id'] == uid for k in KIDS)]
        base_kids = [{'user': k['id'], 'book': ''} for k in KIDS]
        self._json({'parents': out, 'kids': base_kids + kids_out})

    def api_history(self, q):
        kid = (q.get('kid') or [''])[0]
        limit = int((q.get('limit') or ['40'])[0] or 40)
        limit = max(1, min(limit, 300))
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        runs = _stats().get('runs', [])
        if kid:
            runs = [r for r in runs if r.get('kid') == kid]
        self._json({'kid': kid or None, 'runs': runs[:limit]})

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

    def api_report(self, q):
        """Auswertung pro Kind: Gesamtzahlen je Kapitel + Problemwörter (Streak 0, ≥2 Versuche)."""
        kid = (q.get('kid') or [''])[0]
        fkid = self._forced_kid(kid) if auth_has_password() else kid
        if auth_has_password() and fkid is None:
            self._json({'error': 'Parameter fehlen'}, 400)
            return
        kid = fkid
        if not _kid_ok(kid):
            self._json({'error': 'kid unbekannt'}, 400)
            return
        book = (q.get('book') or [''])[0]
        st = _stats()
        agg = _chapter_stats(st, kid)
        seen = st['kid_seen'].get(kid, {})

        # Problemwörter: zuletzt falsch oder nie richtig bei mind. 2 Versuchen
        problems = []
        # Index über alle Bücher/Chapters für en/de-Auflösung
        words_by_qid = {}
        books = [book] if book in _book_ids() else [b['id'] for b in BOOKS]
        for b in books:
            for ch in _qdata(b)['chapters']:
                chnum = str(ch.get('num'))
                for pos, w in enumerate(ch.get('words') or [], 1):
                    if isinstance(w, (list, tuple)) and len(w) >= 2 and _norm(w[0]) and _norm(w[1]):
                        qid = f'{b}|{chnum}|{_norm(w[0]).casefold()}'
                        words_by_qid.setdefault(qid, {'en': _norm(w[0]), 'de': _norm(w[1]),
                                                      'chapter': chnum, 'pos': pos, 'book': b})
        for qid, s in seen.items():
            n = int(s.get('n', 0) or 0)
            right = int(s.get('s', 0) or 0)
            streak = int(s.get('streak', 0) or 0)
            if n >= 2 and (streak == 0 or right == 0):
                info = words_by_qid.get(qid)
                if info:
                    problems.append({'qid': qid, 'en': info['en'], 'de': info['de'],
                                     'chapter': info['chapter'], 'n': n, 'right': right,
                                     'pct': round(100 * right / n), 'last': s.get('last', '')})
        problems.sort(key=lambda p: (p['pct'], -p['n'], p['en']))

        by_chapter = []
        data = _qdata(books[0]) if len(books) == 1 else None
        for akey, a in sorted(agg.items(), key=lambda kv: kv[0]):
            bp, cn = akey.split('|', 1)
            if len(books) > 1 and bp not in books:
                continue
            total = 0
            if data and cn:
                for ch in data['chapters']:
                    if str(ch.get('num')) == cn:
                        total = len(ch.get('words') or [])
                        break
            by_chapter.append({'chapter': cn, 'n': a['n'], 'right': a['right'],
                               'pct': round(100 * a['right'] / a['n']) if a['n'] else 0,
                               'streak': a['streak'], 'total': total})
        self._json({'kid': kid, 'chapters': by_chapter, 'problems': problems[:60]})

    def api_words(self, q):
        """Wortlisten je Kapitel mit Positions-Index (Sichtprüfung/Tests).
        pos = FORTLAUFEND über alle gewählten Kapitel (== gpos des Quiz-Bereich-Filters),
        damit Dropdown-Nummern und erreichbare Vokabeln exakt übereinstimmen."""
        book = (q.get('book') or [''])[0]
        if book not in _book_ids():
            self._json({'error': 'book unbekannt'}, 400)
            return
        only = {c for c in ((q.get('chapters') or [''])[0].split(',')) if c}
        pool = _pool(book, only)
        out = {}
        for it in pool:
            c = out.setdefault(it['num'], {'num': it['num'], 'title': '', 'words': []})
            c['words'].append({'pos': it['gpos'], 'en': it['en'], 'de': it['de']})
        # Titel nachreichen
        data = _qdata(book)
        title_map = {str(ch.get('num')): (ch.get('title') or '') for ch in data['chapters']}
        for num, c in out.items():
            c['title'] = title_map.get(num, '')
            c['count'] = len(c['words'])
        order = {str(ch.get('num')): i for i, ch in enumerate(sorted(data['chapters'], key=lambda ch: int(ch['num']) if str(ch.get('num', 0)).isdigit() else 999))}
        out_l = sorted(out.values(), key=lambda c: order.get(c['num'], 999))
        self._json({'book': book, 'chapters': out_l})

    def api_chapter_save(self, body):
        me = self._session_user() if auth_has_password() else {'role': 'admin'}
        if me and me.get('role') != 'admin':
            self._json({'error': 'Nur der Admin darf Kapitel ändern'}, 403)
            return
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