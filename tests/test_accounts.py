import base64
import json
import tempfile
import unittest
from datetime import timedelta
from contextlib import closing
from unittest.mock import patch
from accounts import AccountStore, GoogleOAuth, hash_token, now, stamp

PROFILE = {'provider':'google','subject':'1234567890','email':'me@example.com','name':'Zhenyu','picture':'https://example.com/a.png'}


def segment(payload):
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return 'header.' + raw + '.signature'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = AccountStore(self.dir.name)

    def test_repeated_login_keeps_one_user_and_updates_profile(self):
        first = self.store.upsert_user(PROFILE)
        second = self.store.upsert_user({**PROFILE, 'name':'段振宇'})
        self.assertEqual(first, second)
        token = self.store.create_session(second)
        self.assertEqual(self.store.user_for_token(token)['name'], '段振宇')

    def test_session_token_is_stored_hashed_and_cleared_on_logout(self):
        token = self.store.create_session(self.store.upsert_user(PROFILE))
        with closing(self.store.connect()) as db, db:
            rows = [row['token_hash'] for row in db.execute('SELECT token_hash FROM sessions')]
        self.assertEqual(rows, [hash_token(token)])
        self.store.delete_session(token)
        self.assertIsNone(self.store.user_for_token(token))

    def test_expired_session_is_rejected_and_fresh_one_rolls_forward(self):
        user = self.store.upsert_user(PROFILE)
        stale = self.store.create_session(user)
        with closing(self.store.connect()) as db, db:
            db.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?',
                       (stamp(now() - timedelta(days=1)), hash_token(stale)))
        self.assertIsNone(self.store.user_for_token(stale))
        self.assertIsNone(self.store.user_for_token('not-a-token'))
        self.assertIsNone(self.store.user_for_token(''))
        token = self.store.create_session(user)
        with closing(self.store.connect()) as db, db:
            db.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?',
                       (stamp(now() + timedelta(days=1)), hash_token(token)))
        self.assertEqual(self.store.user_for_token(token)['id'], user)
        with closing(self.store.connect()) as db, db:
            refreshed = db.execute('SELECT expires_at FROM sessions WHERE token_hash = ?', (hash_token(token),)).fetchone()[0]
        self.assertGreater(refreshed, stamp(now() + timedelta(days=80)))

    def test_merge_is_a_union_and_never_drops_bookmarks(self):
        user = self.store.upsert_user(PROFILE)
        self.store.add_favorite(user, 'microsoft:1')
        self.store.add_favorite(user, 'microsoft:1')
        self.assertEqual(self.store.favorites(user), ['microsoft:1'])
        merged = self.store.merge_favorites(user, ['tencent:2', 'microsoft:1', '  ', None])
        self.assertEqual(sorted(merged), ['microsoft:1', 'tencent:2'])
        self.store.remove_favorite(user, 'microsoft:1')
        self.assertEqual(self.store.favorites(user), ['tencent:2'])

    def test_bookmarks_are_scoped_per_user_and_ids_validated(self):
        mine = self.store.upsert_user(PROFILE)
        other = self.store.upsert_user({**PROFILE, 'subject':'999'})
        self.store.add_favorite(mine, 'apple:1')
        self.assertEqual(self.store.favorites(other), [])
        with self.assertRaises(ValueError): self.store.add_favorite(mine, '')
        with self.assertRaises(ValueError): self.store.add_favorite(mine, 'x' * 201)


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.oauth = GoogleOAuth('client-id.apps.googleusercontent.com', 'secret')

    def claims(self, **extra):
        return {'iss':'https://accounts.google.com', 'aud':'client-id.apps.googleusercontent.com',
                'exp': now().timestamp() + 600, 'sub':'1234567890', 'email':'me@example.com',
                'name':'Zhenyu', 'picture':'https://example.com/a.png', **extra}

    def test_authorize_url_carries_state_and_callback(self):
        url = self.oauth.authorize_url('https://southbound.chillicurry.uk/auth/google/callback', 'abc')
        self.assertIn('state=abc', url)
        self.assertIn('redirect_uri=https%3A%2F%2Fsouthbound.chillicurry.uk%2Fauth%2Fgoogle%2Fcallback', url)
        self.assertIn('scope=openid+email+profile', url)
        self.assertFalse(GoogleOAuth('', '').configured)

    def test_exchange_reads_profile_from_google_token_response(self):
        with patch.object(GoogleOAuth, 'post', return_value={'id_token': segment(self.claims())}):
            profile = self.oauth.exchange('code', 'https://example.com/cb')
        self.assertEqual(profile, PROFILE)

    def test_tokens_for_another_app_or_issuer_are_refused(self):
        for bad in ({'aud':'someone-else'}, {'iss':'https://evil.example'}, {'exp': now().timestamp() - 10}, {'sub':''}):
            with patch.object(GoogleOAuth, 'post', return_value={'id_token': segment(self.claims(**bad))}):
                with self.assertRaises(ValueError): self.oauth.exchange('code', 'https://example.com/cb')
        with patch.object(GoogleOAuth, 'post', return_value={'error':'invalid_grant'}):
            with self.assertRaises(ValueError): self.oauth.exchange('code', 'https://example.com/cb')
        with self.assertRaises(ValueError): GoogleOAuth('', '').exchange('code', 'https://example.com/cb')


if __name__ == '__main__':
    unittest.main()
