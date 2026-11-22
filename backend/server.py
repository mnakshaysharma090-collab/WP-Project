#!/usr/bin/env python3
"""
CivicPulse backend.

Standard library only (http.server + sqlite3), so no packages need installing.

Run the site and API:     python3 backend/server.py          -> http://localhost:8000
Create an admin account:  python3 backend/server.py create-admin you@example.com

Environment variables:
  PORT                       port to listen on (default 8000)
  HOST                       address to bind (default 127.0.0.1)
  CIVICPULSE_DB              SQLite file (default backend/civicpulse.db)
  CIVICPULSE_ADMIN_PASSWORD  admin password for create-admin (skips the prompt, for scripting)
"""
import base64
import binascii
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import traceback
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BACKEND_DIR)                      # the website folder
UPLOAD_DIR = os.path.join(ROOT, 'uploads')
DB_PATH = os.environ.get('CIVICPULSE_DB', os.path.join(BACKEND_DIR, 'civicpulse.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_MYSQL = DATABASE_URL.startswith(('mysql://', 'mysql+pymysql://'))
PORT = int(os.environ.get('PORT', '8000'))
HOST = os.environ.get('HOST', '127.0.0.1')

CATEGORIES = ['Drainage', 'Roads', 'Streetlights', 'Waste Management', 'Water Supply', 'Other']
STATUSES = ['Pending', 'In Progress', 'Resolved']
SESSION_DAYS = 7
PASSWORD_ITERATIONS = 200_000
MAX_BODY_BYTES = 8 * 1024 * 1024          # request size limit (photos arrive as base64)
MAX_PHOTO_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
STATIC_TYPES = {
    '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8', '.svg': 'image/svg+xml',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp',
}
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'citizen' CHECK (role IN ('citizen', 'admin')),
    created_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    locality    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'In Progress', 'Resolved')),
    photo_url   TEXT,
    reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issue_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_issue ON issue_events(issue_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(80)  NOT NULL,
    email         VARCHAR(191) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(10)  NOT NULL DEFAULT 'citizen',
    created_at    VARCHAR(40)  NOT NULL,
    CHECK (role IN ('citizen', 'admin'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sessions (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id    INT         NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    INDEX idx_sessions_user (user_id),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS issues (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(120) NOT NULL,
    description TEXT         NOT NULL,
    category    VARCHAR(40)  NOT NULL,
    locality    VARCHAR(100) NOT NULL,
    status      VARCHAR(20)  NOT NULL DEFAULT 'Pending',
    photo_url   VARCHAR(255) NULL,
    reporter_id INT          NULL,
    created_at  VARCHAR(40)  NOT NULL,
    updated_at  VARCHAR(40)  NOT NULL,
    CHECK (status IN ('Pending', 'In Progress', 'Resolved')),
    CONSTRAINT fk_issues_reporter FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS issue_events (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    issue_id   INT          NOT NULL,
    status     VARCHAR(20)  NOT NULL,
    note       VARCHAR(500) NOT NULL DEFAULT '',
    created_at VARCHAR(40)  NOT NULL,
    INDEX idx_events_issue (issue_id),
    CONSTRAINT fk_events_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Sample issues loaded into an empty database: id, title, description, category, locality, status, reported, photo
SEED_ISSUES = [
    (1001, 'Pothole on Main Road', 'A deep pothole in the middle of the main road is making traffic swerve and is a risk for two-wheelers.', 'Roads', 'Arera Colony', 'In Progress', '2026-10-14', 'image/pothole.jpg'),
    (1002, 'Garbage Accumulation', 'Garbage has been piling up beside the road for several days and is attracting stray animals.', 'Waste Management', 'MP Nagar', 'Resolved', '2026-10-03', 'image/dustbin.jpg'),
    (1003, 'Broken Streetlight', 'The streetlight near the bus stop has been out since last week, which makes the stop unsafe after dark.', 'Streetlights', 'Shivaji Nagar', 'Pending', '2026-11-12', 'image/streetlight.jpg'),
    (1004, 'Water Pipeline Leakage', 'A water pipeline is leaking onto the road and water is being wasted every day.', 'Water Supply', 'Kolar Road', 'In Progress', '2026-10-22', 'image/water_leakage.jpg'),
    (1005, 'Overflowing Drain', 'The drain overflows onto the footpath after rain, and the smell is getting worse.', 'Drainage', 'Habibganj', 'Pending', '2026-11-17', 'image/DRAIN.jpg'),
    (1006, 'Damaged Footpath', 'Broken tiles on the footpath make it hard for pedestrians and wheelchair users to pass.', 'Roads', 'New Market', 'Resolved', '2026-09-28', 'image/footpath.jpg'),
]


# ---------------------------------------------------------------- helpers

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Db:
    """One connection, same interface for SQLite and MySQL.

    Queries use '?' placeholders everywhere; MySQL's '%s' is substituted here.
    """

    def __init__(self):
        if USE_MYSQL:
            if pymysql is None:
                raise RuntimeError('MySQL is configured but PyMySQL is not installed. Run: pip install pymysql')
            url = urlparse(DATABASE_URL)
            self.conn = pymysql.connect(
                host=url.hostname or '127.0.0.1',
                port=url.port or 3306,
                user=unquote(url.username or ''),
                password=unquote(url.password or ''),
                database=unquote(url.path.lstrip('/')),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
            )
            self.mysql = True
        else:
            self.conn = sqlite3.connect(DB_PATH, timeout=10)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA foreign_keys = ON')
            self.mysql = False

    def execute(self, sql, params=()):
        if self.mysql:
            sql = sql.replace('?', '%s')
        cur = self.conn.cursor()
        cur.execute(sql, tuple(params))
        return cur

    def run_schema(self, schema):
        if self.mysql:
            for statement in schema.split(';'):
                if statement.strip():
                    self.execute(statement)
        else:
            self.conn.executescript(schema)
        self.commit()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


INTEGRITY_ERROR = pymysql.err.IntegrityError if (USE_MYSQL and pymysql) else sqlite3.IntegrityError


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)
    return 'pbkdf2_sha256$%d$%s$%s' % (PASSWORD_ITERATIONS, salt.hex(), digest.hex())


def verify_password(password, stored):
    try:
        _, iterations, salt_hex, digest_hex = stored.split('$')
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def clean_text(value, field, min_len, max_len):
    if not isinstance(value, str):
        raise ApiError(400, '%s is required.' % field)
    value = value.strip()
    if len(value) < min_len:
        raise ApiError(400, '%s must be at least %d characters.' % (field, min_len))
    if len(value) > max_len:
        raise ApiError(400, '%s must be under %d characters.' % (field, max_len))
    return value


def clean_email(value):
    email = clean_text(value, 'Email', 3, 191).lower()
    if not EMAIL_RE.match(email):
        raise ApiError(400, 'Please enter a valid email address.')
    return email


def clean_password(value):
    if not isinstance(value, str) or len(value) < 8:
        raise ApiError(400, 'Password must be at least 8 characters.')
    if len(value) > 200:
        raise ApiError(400, 'Password is too long.')
    return value


def parse_issue_code(code):
    match = re.fullmatch(r'CP-(\d{1,9})', code.strip().upper())
    if not match:
        raise ApiError(404, 'No complaint found with ID %s. Check the ID and try again.' % code)
    return int(match.group(1))


def issue_code(issue_id):
    return 'CP-%d' % issue_id


def public_issue(row, with_description=False):
    data = {
        'id': issue_code(row['id']),
        'title': row['title'],
        'locality': row['locality'],
        'category': row['category'],
        'status': row['status'],
        'date': row['created_at'][:10],
        'photo_url': row['photo_url'],
    }
    if with_description:
        data['description'] = row['description']
        data['updated_at'] = row['updated_at']
    return data


def public_user(row):
    return {'id': row['id'], 'name': row['name'], 'email': row['email'], 'role': row['role']}


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec='seconds')
    conn.execute('INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)',
                 (hash_token(token), user_id, expires))
    return token


def current_user(conn, handler):
    try:
        jar = cookies.SimpleCookie(handler.headers.get('Cookie', ''))
    except cookies.CookieError:
        return None
    morsel = jar.get('cp_session')
    if not morsel:
        return None
    conn.execute('DELETE FROM sessions WHERE expires_at < ?', (now_iso(),))
    return conn.execute(
        'SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?',
        (hash_token(morsel.value),)
    ).fetchone()


def seed_timeline(status, reported):
    """Status history for the sample issues: (status, note, timestamp)."""
    start = datetime.fromisoformat(reported + 'T09:00:00+00:00')
    events = [('Pending', 'Report received.', start)]
    if status in ('In Progress', 'Resolved'):
        events.append(('In Progress', 'Assigned to the local team.', start + timedelta(days=2)))
    if status == 'Resolved':
        events.append(('Resolved', 'Work completed.', start + timedelta(days=9)))
    return events


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    with closing(Db()) as conn:
        conn.run_schema(SCHEMA_MYSQL if USE_MYSQL else SCHEMA_SQLITE)
        if conn.execute('SELECT COUNT(*) AS n FROM issues').fetchone()['n'] == 0:
            for iid, title, desc, cat, loc, status, reported, photo in SEED_ISSUES:
                events = seed_timeline(status, reported)
                last = events[-1][2].isoformat(timespec='seconds')
                conn.execute(
                    'INSERT INTO issues (id, title, description, category, locality, status, photo_url, '
                    'reporter_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)',
                    (iid, title, desc, cat, loc, status, photo, events[0][2].isoformat(timespec='seconds'), last))
                for ev_status, note, when in events:
                    conn.execute('INSERT INTO issue_events (issue_id, status, note, created_at) VALUES (?, ?, ?, ?)',
                                 (iid, ev_status, note, when.isoformat(timespec='seconds')))
        conn.commit()


# ---------------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    server_version = 'CivicPulse'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def do_PATCH(self):
        self.route('PATCH')

    def route(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith('/api/'):
                self.handle_api(method, path, parse_qs(parsed.query))
            elif method == 'GET':
                self.serve_static(path)
            else:
                raise ApiError(405, 'Method not allowed.')
        except ApiError as err:
            self.send_json(err.status, {'error': err.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {'error': 'Something went wrong on the server. Please try again.'})

    # ---- request/response plumbing

    def read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            raise ApiError(400, 'Request body is required.')
        if length > MAX_BODY_BYTES:
            raise ApiError(413, 'The request is too large.')
        try:
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, 'The request body is not valid JSON.')
        if not isinstance(data, dict):
            raise ApiError(400, 'The request body must be a JSON object.')
        return data

    def send_json(self, status, payload, set_cookie=None):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if set_cookie:
            self.send_header('Set-Cookie', set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def session_cookie(self, token):
        return 'cp_session=%s; HttpOnly; Path=/; SameSite=Lax; Max-Age=%d' % (token, SESSION_DAYS * 86400)

    def clear_cookie(self):
        return 'cp_session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0'

    def require_admin(self, conn):
        user = current_user(conn, self)
        if user is None:
            raise ApiError(401, 'Please sign in first.')
        if user['role'] != 'admin':
            raise ApiError(403, 'Only administrators can do that.')
        return user

    # ---- API routing

    def handle_api(self, method, path, query):
        if path == '/api/stats' and method == 'GET':
            return self.api_stats()
        if path == '/api/issues':
            if method == 'GET':
                return self.api_list_issues(query)
            if method == 'POST':
                return self.api_create_issue()
        match = re.fullmatch(r'/api/issues/([A-Za-z]{2}-\d+)', path)
        if match and method == 'GET':
            return self.api_get_issue(match.group(1))
        match = re.fullmatch(r'/api/admin/issues/([A-Za-z]{2}-\d+)', path)
        if match and method == 'PATCH':
            return self.api_admin_update(match.group(1))
        if path == '/api/auth/register' and method == 'POST':
            return self.api_register()
        if path == '/api/auth/login' and method == 'POST':
            return self.api_login()
        if path == '/api/auth/logout' and method == 'POST':
            return self.api_logout()
        if path == '/api/auth/me' and method == 'GET':
            return self.api_me()
        raise ApiError(404, 'Not found.')

    # ---- issues

    def api_stats(self):
        with closing(Db()) as conn:
            counts = {row['status']: row['n'] for row in conn.execute(
                'SELECT status, COUNT(*) AS n FROM issues GROUP BY status')}
            citizens = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'citizen'").fetchone()['n']
        pending = counts.get('Pending', 0)
        in_progress = counts.get('In Progress', 0)
        resolved = counts.get('Resolved', 0)
        self.send_json(200, {
            'reported': pending + in_progress + resolved,
            'pending': pending,
            'in_progress': in_progress,
            'resolved': resolved,
            'citizens': citizens,
        })

    def api_list_issues(self, query):
        clauses, params = [], []
        category = (query.get('category') or [''])[0]
        if category and category != 'all':
            if category not in CATEGORIES:
                raise ApiError(400, 'Unknown category.')
            clauses.append('category = ?')
            params.append(category)
        status = (query.get('status') or [''])[0]
        if status and status != 'all':
            if status not in STATUSES:
                raise ApiError(400, 'Unknown status.')
            clauses.append('status = ?')
            params.append(status)
        search = (query.get('q') or [''])[0].strip()[:100]
        if search:
            escaped = search.replace('!', '!!').replace('%', '!%').replace('_', '!_')
            like = '%' + escaped + '%'
            clauses.append("(title LIKE ? ESCAPE '!' OR locality LIKE ? ESCAPE '!' OR description LIKE ? ESCAPE '!')")
            params.extend([like, like, like])
        try:
            limit = min(max(int((query.get('limit') or ['500'])[0]), 1), 500)
        except ValueError:
            raise ApiError(400, 'Limit must be a number.')
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with closing(Db()) as conn:
            rows = conn.execute('SELECT * FROM issues' + where + ' ORDER BY id DESC LIMIT %d' % limit, params).fetchall()
        self.send_json(200, {'issues': [public_issue(r) for r in rows]})

    def api_get_issue(self, code):
        issue_id = parse_issue_code(code)
        with closing(Db()) as conn:
            row = conn.execute('SELECT * FROM issues WHERE id = ?', (issue_id,)).fetchone()
            if row is None:
                raise ApiError(404, 'No complaint found with ID %s. Check the ID and try again.' % code.upper())
            events = conn.execute(
                'SELECT status, note, created_at FROM issue_events WHERE issue_id = ? ORDER BY id', (issue_id,)).fetchall()
        self.send_json(200, {
            'issue': public_issue(row, with_description=True),
            'events': [{'status': e['status'], 'note': e['note'], 'date': e['created_at']} for e in events],
        })

    def api_create_issue(self):
        data = self.read_json()
        title = clean_text(data.get('title'), 'Title', 3, 120)
        category = data.get('category')
        if category not in CATEGORIES:
            raise ApiError(400, 'Please choose a category.')
        locality = clean_text(data.get('locality'), 'Locality', 2, 100)
        description = clean_text(data.get('description'), 'Description', 10, 2000)

        photo_url = None
        photo_data = data.get('photo')
        if photo_data:
            photo_url = self.save_photo(photo_data)

        with closing(Db()) as conn:
            reporter = current_user(conn, self)
            created = now_iso()
            try:
                cur = conn.execute(
                    'INSERT INTO issues (title, description, category, locality, status, photo_url, reporter_id, '
                    'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (title, description, category, locality, 'Pending', photo_url,
                     reporter['id'] if reporter else None, created, created))
                conn.execute('INSERT INTO issue_events (issue_id, status, note, created_at) VALUES (?, ?, ?, ?)',
                             (cur.lastrowid, 'Pending', 'Report received.', created))
                conn.commit()
            except Exception:
                conn.rollback()
                if photo_url:
                    self.remove_photo(photo_url)
                raise
            row = conn.execute('SELECT * FROM issues WHERE id = ?', (cur.lastrowid,)).fetchone()
        self.send_json(201, {'issue': public_issue(row, with_description=True)})

    def save_photo(self, data_url):
        match = re.fullmatch(r'data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)', str(data_url), re.S)
        if not match or match.group(1) not in IMAGE_TYPES:
            raise ApiError(400, 'Photo must be a JPEG, PNG, WebP or GIF image.')
        try:
            raw = base64.b64decode(re.sub(r'\s', '', match.group(2)), validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, 'The photo could not be read.')
        if len(raw) > MAX_PHOTO_BYTES:
            raise ApiError(400, 'Photo must be under 5 MB.')
        filename = uuid.uuid4().hex + IMAGE_TYPES[match.group(1)]
        with open(os.path.join(UPLOAD_DIR, filename), 'wb') as fh:
            fh.write(raw)
        return '/uploads/' + filename

    def remove_photo(self, url):
        path = os.path.join(UPLOAD_DIR, os.path.basename(url))
        if os.path.isfile(path):
            os.remove(path)

    def api_admin_update(self, code):
        issue_id = parse_issue_code(code)
        data = self.read_json()
        status = data.get('status')
        if status not in STATUSES:
            raise ApiError(400, 'Choose a valid status.')
        note = data.get('note', '')
        if not isinstance(note, str):
            raise ApiError(400, 'Note must be text.')
        note = note.strip()
        if len(note) > 300:
            raise ApiError(400, 'Note must be under 300 characters.')

        with closing(Db()) as conn:
            self.require_admin(conn)
            row = conn.execute('SELECT * FROM issues WHERE id = ?', (issue_id,)).fetchone()
            if row is None:
                raise ApiError(404, 'No complaint found with ID %s.' % code.upper())
            if row['status'] == status and not note:
                raise ApiError(400, 'Nothing to change. Pick a different status or add a note.')
            stamp = now_iso()
            conn.execute('UPDATE issues SET status = ?, updated_at = ? WHERE id = ?', (status, stamp, issue_id))
            conn.execute('INSERT INTO issue_events (issue_id, status, note, created_at) VALUES (?, ?, ?, ?)',
                         (issue_id, status, note or 'Status changed to %s.' % status, stamp))
            conn.commit()
            row = conn.execute('SELECT * FROM issues WHERE id = ?', (issue_id,)).fetchone()
        self.send_json(200, {'issue': public_issue(row, with_description=True)})

    # ---- accounts

    def api_register(self):
        data = self.read_json()
        name = clean_text(data.get('name'), 'Name', 2, 80)
        email = clean_email(data.get('email'))
        password = clean_password(data.get('password'))
        with closing(Db()) as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'citizen', ?)",
                    (name, email, hash_password(password), now_iso()))
            except INTEGRITY_ERROR:
                raise ApiError(409, 'An account with this email already exists. Try logging in instead.')
            token = create_session(conn, cur.lastrowid)
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE id = ?', (cur.lastrowid,)).fetchone()
        self.send_json(201, {'user': public_user(user)}, self.session_cookie(token))

    def api_login(self):
        data = self.read_json()
        email = clean_email(data.get('email'))
        password = data.get('password')
        if not isinstance(password, str) or not password:
            raise ApiError(400, 'Please enter your password.')
        with closing(Db()) as conn:
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            if user is None or not verify_password(password, user['password_hash']):
                raise ApiError(401, 'Email or password is incorrect.')
            token = create_session(conn, user['id'])
            conn.commit()
        self.send_json(200, {'user': public_user(user)}, self.session_cookie(token))

    def api_logout(self):
        try:
            jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
            morsel = jar.get('cp_session')
        except cookies.CookieError:
            morsel = None
        if morsel:
            with closing(Db()) as conn:
                conn.execute('DELETE FROM sessions WHERE token_hash = ?', (hash_token(morsel.value),))
                conn.commit()
        self.send_json(200, {'ok': True}, self.clear_cookie())

    def api_me(self):
        with closing(Db()) as conn:
            user = current_user(conn, self)
        self.send_json(200, {'user': public_user(user) if user else None})

    # ---- static files

    def serve_static(self, path):
        if path in ('', '/'):
            path = '/index.html'
        rel = path.lstrip('/')
        full = os.path.realpath(os.path.join(ROOT, rel))
        root = os.path.realpath(ROOT)
        ext = os.path.splitext(full)[1].lower()
        blocked = rel.split('/')[0].startswith('.') or rel.split('/')[0] == 'backend'
        if blocked or not full.startswith(root + os.sep) or ext not in STATIC_TYPES or not os.path.isfile(full):
            body = b'Not found'
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        with open(full, 'rb') as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', STATIC_TYPES[ext])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------- command line

def create_admin(email):
    email = clean_email(email)
    password = os.environ.get('CIVICPULSE_ADMIN_PASSWORD') or getpass.getpass('Admin password (min 8 characters): ')
    clean_password(password)
    init_db()
    with closing(Db()) as conn:
        existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET role = 'admin', password_hash = ? WHERE id = ?",
                         (hash_password(password), existing['id']))
            action = 'Updated %s to admin.' % email
        else:
            conn.execute("INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                         ('Administrator', email, hash_password(password), now_iso()))
            action = 'Created admin account %s.' % email
        conn.commit()
    print(action)


def main(argv):
    if len(argv) > 1 and argv[1] == 'create-admin':
        if len(argv) < 3:
            print('Usage: python3 backend/server.py create-admin you@example.com')
            return 1
        try:
            create_admin(argv[2])
        except ApiError as err:
            print(err.message)
            return 1
        return 0

    init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    if USE_MYSQL:
        target = 'MySQL at ' + urlparse(DATABASE_URL).hostname + '/' + urlparse(DATABASE_URL).path.lstrip('/')
    else:
        target = 'SQLite at ' + DB_PATH
    print('CivicPulse running at http://%s:%d  (database: %s)' % (HOST, PORT, target))
    print('Press Ctrl+C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))