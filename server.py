"""Run: python server.py. Local dashboard + daily 09:00 UTC+8 sync."""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, parse_qs, quote, unquote
from collector import ROOT, TZ, LOCK, read_data, refresh, is_running, DETAILS
from jd_translation import TranslationStore
from accounts import AccountStore, GoogleOAuth, SESSION_COOKIE, SESSION_DAYS, STATE_COOKIE, STATE_TTL, new_state

TRANSLATIONS = TranslationStore()
ACCOUNTS = AccountStore()
GOOGLE = GoogleOAuth()

PORT = int(os.environ.get('PORT', '8765'))
PUBLIC_ORIGIN = os.environ.get('SOUTHBOUND_PUBLIC_ORIGIN', '').rstrip('/')
PUBLIC_HOST = urlsplit(PUBLIC_ORIGIN).netloc
CODE_ROOT = Path(__file__).resolve().parent

def should_refresh(last, current):
    return not last or (current.hour >= 9 and last[:10] != current.date().isoformat())

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CODE_ROOT / 'public'), **kwargs)

    def list_directory(self, path):
        self.send_error(404)
        return None

    def translate_path(self, path):
        resolved = Path(super().translate_path(path))
        relative = resolved.relative_to(CODE_ROOT / 'public')
        if relative.parts and relative.parts[0] in {'snapshot.js', 'details', 'translations'}:
            return str(ROOT / 'public' / relative)
        return str(resolved)

    def json_response(self, data, code=200, cookies=()):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for name, value, age in cookies:
            self.set_cookie(name, value, age)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self, name):
        item = SimpleCookie(self.headers.get('Cookie', '')).get(name)
        return item.value if item else ''

    def set_cookie(self, name, value, age):
        secure = '; Secure' if PUBLIC_ORIGIN else ''
        self.send_header('Set-Cookie', f'{name}={value}; Path=/; Max-Age={age}; HttpOnly; SameSite=Lax{secure}')

    def redirect(self, location, cookies=()):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        for name, value, age in cookies:
            self.set_cookie(name, value, age)
        self.end_headers()

    def current_user(self):
        try:
            return ACCOUNTS.user_for_token(self.cookie(SESSION_COOKIE))
        except Exception as error:
            print(f'Session: {error}', flush=True)
            return None

    def same_origin(self):
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin', '')
        if PUBLIC_ORIGIN:
            return host == PUBLIC_HOST and origin == PUBLIC_ORIGIN
        return host in {f'localhost:{PORT}', f'127.0.0.1:{PORT}'} and origin == f'http://{host}'

    def callback_uri(self):
        host = self.headers.get('Host', '') or f'localhost:{PORT}'
        return (PUBLIC_ORIGIN or 'http://' + host) + '/auth/google/callback'

    def safe_target(self, value):
        return value if value.startswith('/') and not value.startswith('//') else '/'

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/jobs': return self.json_response(read_data())
        if path == '/api/me':
            user = self.current_user()
            favorites = ACCOUNTS.favorites(user['id']) if user else []
            return self.json_response({'user': user, 'favorites': favorites, 'googleEnabled': GOOGLE.configured})
        if path == '/auth/google/start':
            if not GOOGLE.configured: return self.redirect('/?login=unavailable')
            target = self.safe_target(parse_qs(urlsplit(self.path).query).get('next', ['/'])[0])
            state = new_state()
            return self.redirect(GOOGLE.authorize_url(self.callback_uri(), state),
                                 [(STATE_COOKIE, state + '~' + quote(target, safe=''), STATE_TTL)])
        if path == '/auth/google/callback':
            query = parse_qs(urlsplit(self.path).query)
            state, _, encoded = self.cookie(STATE_COOKIE).partition('~')
            target = self.safe_target(unquote(encoded) or '/')
            code = query.get('code', [''])[0]
            if not code or not state or query.get('state', [''])[0] != state:
                return self.redirect('/?login=failed', [(STATE_COOKIE, '', 0)])
            try:
                token = ACCOUNTS.create_session(ACCOUNTS.upsert_user(GOOGLE.exchange(code, self.callback_uri())))
            except Exception as error:
                print(f'Google login: {error}', flush=True)
                return self.redirect('/?login=failed', [(STATE_COOKIE, '', 0)])
            return self.redirect(target, [(STATE_COOKIE, '', 0), (SESSION_COOKIE, token, SESSION_DAYS * 86400)])
        if path == '/api/status': return self.json_response({'running': is_running(), 'schedule': '每天 09:00（UTC+8）', 'mode': 'server' if PUBLIC_ORIGIN else 'local', 'canRefresh': not bool(PUBLIC_ORIGIN)})
        if path == '/api/job-translation':
            id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            job = next((j for j in read_data()['jobs'] if j['id'] == id), None)
            if job is None: return self.json_response({'error': '职位不存在'}, 404)
            try: return self.json_response(TRANSLATIONS.read(DETAILS.get(job)))
            except Exception: return self.json_response({'status': 'error', 'message': '暂时无法读取原文，请重试。'}, 502)
        if path == '/api/job-detail':
            id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            job = next((j for j in read_data()['jobs'] if j['id'] == id), None)
            if job is None: return self.json_response({'error': '职位不存在'}, 404)
            try: return self.json_response(DETAILS.get(job))
            except Exception:
                return self.json_response({'error': '暂时无法读取官方完整 JD，请稍后重试。'}, 502)
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in {'/api/refresh', '/api/translate', '/api/auth/logout', '/api/favorites'}: return self.json_response({'error': 'Not found'}, 404)
        if not self.same_origin() or self.headers.get('X-Requested-With') != 'SouthboundJobs':
            return self.json_response({'error': '请求来源不允许'}, 403)
        if path == '/api/auth/logout':
            ACCOUNTS.delete_session(self.cookie(SESSION_COOKIE))
            return self.json_response({'ok': True}, cookies=[(SESSION_COOKIE, '', 0)])
        if path == '/api/favorites':
            user = self.current_user()
            if user is None: return self.json_response({'error': '请先登录'}, 401)
            try:
                length = min(int(self.headers.get('Content-Length', '0') or 0), 200000)
                payload = json.loads(self.rfile.read(length) or b'{}') if length else {}
                action = payload.get('action')
                if action == 'add': ACCOUNTS.add_favorite(user['id'], payload.get('id', ''))
                elif action == 'remove': ACCOUNTS.remove_favorite(user['id'], payload.get('id', ''))
                elif action == 'merge': ACCOUNTS.merge_favorites(user['id'], payload.get('ids', []))
                elif action != 'list': return self.json_response({'error': '不支持的操作'}, 400)
            except ValueError: return self.json_response({'error': '请求格式错误'}, 400)
            return self.json_response({'favorites': ACCOUNTS.favorites(user['id'])})
        if path == '/api/refresh' and PUBLIC_ORIGIN:
            return self.json_response({'error': '请由管理员在服务器执行同步'}, 403)
        if path == '/api/translate':
            id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            job = next((j for j in read_data()['jobs'] if j['id'] == id), None)
            if job is None: return self.json_response({'error': '职位不存在'}, 404)
            try:
                result = TRANSLATIONS.start(DETAILS.get(job))
                return self.json_response(result, 202 if result['status'] == 'pending' else 200)
            except Exception: return self.json_response({'status': 'error', 'message': '暂时无法读取原文，请重试。'}, 502)
        if is_running(): return self.json_response({'running': True}, 202)
        threading.Thread(target=refresh, daemon=True).start()
        self.json_response({'running': True}, 202)

def schedule():
    while True:
        try:
            data = read_data()
            current = datetime.now(TZ)
            last = data.get('updatedAt')
            # Catch up at startup when yesterday's data is present; then once per day after 09:00.
            due = should_refresh(last, current)
            if due and not is_running(): refresh()
        except Exception as e:
            print(f'Scheduler: {e}', flush=True)
        time.sleep(60)

if __name__ == '__main__':
    if os.environ.get('SOUTHBOUND_SCHEDULER', '1') == '1':
        threading.Thread(target=schedule, daemon=True).start()
    print(f'Southbound Jobs: http://localhost:{PORT} | daily 09:00 UTC+8', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
