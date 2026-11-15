"""Accounts, sessions and server-side bookmarks. Standard library only."""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get('SOUTHBOUND_DATA_ROOT', Path(__file__).resolve().parent))
SESSION_DAYS = 90
SESSION_COOKIE = 'sb_session'
STATE_COOKIE = 'sb_oauth'
STATE_TTL = 600
AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
SCOPES = 'openid email profile'
ISSUERS = {'https://accounts.google.com', 'accounts.google.com'}

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        email TEXT,
        name TEXT,
        picture TEXT,
        created_at TEXT NOT NULL,
        last_login TEXT NOT NULL,
        UNIQUE (provider, subject))''',
    '''CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        job_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, job_id))''',
    'CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)',
)


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.replace(microsecond=0).isoformat()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def decode_segment(segment):
    padded = segment + '=' * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()))


class GoogleOAuth:
    """Server-side authorization code flow. The id_token arrives over our own
    TLS request to Google's token endpoint, so no local signature check is needed."""

    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id if client_id is not None else os.environ.get('SOUTHBOUND_GOOGLE_CLIENT_ID', '')
        self.client_secret = client_secret if client_secret is not None else os.environ.get('SOUTHBOUND_GOOGLE_CLIENT_SECRET', '')

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, redirect_uri, state):
        query = urllib.parse.urlencode({
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': SCOPES,
            'state': state,
            'access_type': 'online',
            'prompt': 'select_account',
            'include_granted_scopes': 'true',
        })
        return AUTH_ENDPOINT + '?' + query

    def post(self, body):
        request = urllib.request.Request(TOKEN_ENDPOINT, data=urllib.parse.urlencode(body).encode(),
                                         headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                  'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def exchange(self, code, redirect_uri):
        if not self.configured:
            raise ValueError('Google login is not configured')
        payload = self.post({
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        })
        token = payload.get('id_token')
        if not token or token.count('.') != 2:
            raise ValueError('Google did not return an id_token')
        claims = decode_segment(token.split('.')[1])
        if claims.get('iss') not in ISSUERS:
            raise ValueError('Unexpected issuer')
        if claims.get('aud') != self.client_id:
            raise ValueError('Unexpected audience')
        if float(claims.get('exp', 0)) < now().timestamp():
            raise ValueError('Expired id_token')
        subject = claims.get('sub')
        if not subject:
            raise ValueError('Missing subject')
        return {
            'provider': 'google',
            'subject': subject,
            'email': claims.get('email') or '',
            'name': claims.get('name') or (claims.get('email') or '').split('@')[0],
            'picture': claims.get('picture') or '',
        }


class AccountStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = self.root / 'accounts.sqlite3'
        self.ready = False

    def connect(self):
        if not self.ready:
            self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        if not self.ready:
            with db:
                for statement in SCHEMA:
                    db.execute(statement)
            self.ready = True
        return db

    def upsert_user(self, profile):
        moment = stamp(now())
        with closing(self.connect()) as db, db:
            db.execute('''INSERT INTO users (provider, subject, email, name, picture, created_at, last_login)
                          VALUES (?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT (provider, subject) DO UPDATE SET
                            email=excluded.email, name=excluded.name,
                            picture=excluded.picture, last_login=excluded.last_login''',
                       (profile['provider'], profile['subject'], profile.get('email', ''),
                        profile.get('name', ''), profile.get('picture', ''), moment, moment))
            row = db.execute('SELECT id FROM users WHERE provider=? AND subject=?',
                             (profile['provider'], profile['subject'])).fetchone()
        return row['id']

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        created = now()
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)',
                       (hash_token(token), user_id, stamp(created),
                        stamp(created + timedelta(days=SESSION_DAYS))))
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (stamp(created),))
        return token

    def user_for_token(self, token):
        if not token:
            return None
        with closing(self.connect()) as db, db:
            row = db.execute('''SELECT u.id, u.email, u.name, u.picture, s.expires_at
                                FROM sessions s JOIN users u ON u.id = s.user_id
                                WHERE s.token_hash = ?''', (hash_token(token),)).fetchone()
            if row is None:
                return None
            moment = now()
            if row['expires_at'] < stamp(moment):
                db.execute('DELETE FROM sessions WHERE token_hash = ?', (hash_token(token),))
                return None
            # Rolling expiry: refresh when less than two thirds of the window remains.
            fresh = stamp(moment + timedelta(days=SESSION_DAYS))
            if row['expires_at'] < stamp(moment + timedelta(days=SESSION_DAYS * 2 // 3)):
                db.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?', (fresh, hash_token(token)))
        return {'id': row['id'], 'email': row['email'], 'name': row['name'], 'picture': row['picture']}

    def delete_session(self, token):
        if not token:
            return
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM sessions WHERE token_hash = ?', (hash_token(token),))

    def favorites(self, user_id):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT job_id FROM favorites WHERE user_id = ? ORDER BY created_at DESC, job_id',
                              (user_id,)).fetchall()
        return [row['job_id'] for row in rows]

    def add_favorite(self, user_id, job_id):
        job_id = (job_id or '').strip()
        if not job_id or len(job_id) > 200:
            raise ValueError('Invalid job id')
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO favorites (user_id, job_id, created_at) VALUES (?, ?, ?)',
                       (user_id, job_id, stamp(now())))

    def remove_favorite(self, user_id, job_id):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM favorites WHERE user_id = ? AND job_id = ?', (user_id, job_id))

    def merge_favorites(self, user_id, job_ids):
        """Union, never overwrite: signing in on a new device must not drop bookmarks."""
        moment = stamp(now())
        rows = []
        for job_id in job_ids or []:
            value = job_id.strip() if isinstance(job_id, str) else ''
            if value and len(value) <= 200:
                rows.append((user_id, value, moment))
        rows = rows[:2000]
        if rows:
            with closing(self.connect()) as db, db:
                db.executemany('INSERT OR IGNORE INTO favorites (user_id, job_id, created_at) VALUES (?, ?, ?)', rows)
        return self.favorites(user_id)


def new_state():
    return secrets.token_urlsafe(24)
