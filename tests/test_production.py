import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server
from jd_translation import AIProvider, TranslationStore

class ProductionTests(unittest.TestCase):
    def test_bearer_token_headers_and_exact_model(self):
        env={'JD_TRANSLATION_AUTH_TOKEN':'test-token','JD_TRANSLATION_BASE_URL':'https://api.example.com','JD_TRANSLATION_MODEL':'gemini-3.7-flash'}
        with patch.dict(os.environ,env,clear=True): provider=AIProvider()
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return json.dumps({'stop_reason':'end_turn','content':[{'type':'text','text':'{"0":"至少 3 年经验"}'}]}).encode()
        with patch('jd_translation.urllib.request.urlopen',return_value=Reply()) as call:
            self.assertEqual(provider.translate({'0':'At least 3 years experience'})['0'],'至少 3 年经验')
        req=call.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'),'Bearer test-token')
        self.assertEqual(json.loads(req.data)['model'],'gemini-3.7-flash')
        self.assertEqual(req.get_header('User-agent'),'SouthboundJobs/1.0')
        self.assertIsNone(req.get_header('X-api-key'))

    def test_daily_cap_persists_across_store_restart(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'JD_TRANSLATION_DAILY_LIMIT':'1'}):
            a=TranslationStore(root=root)
            try: self.assertTrue(a.reserve_budget())
            finally: a.pool.shutdown()
            b=TranslationStore(root=root)
            try: self.assertFalse(b.reserve_budget())
            finally: b.pool.shutdown()

    def test_public_refresh_blocked_cross_origin_rejected_and_status(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server,'ROOT',Path(root)), patch.object(server,'PUBLIC_ORIGIN','https://jobs.example.com'), patch.object(server,'PUBLIC_HOST','jobs.example.com'), patch.object(server,'is_running',return_value=False):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(http.server_port)
            try:
                for origin in ['https://jobs.example.com','https://bad.example.com']:
                    req=urllib.request.Request(base+'/api/refresh',method='POST',headers={'Host':'jobs.example.com','Origin':origin,'X-Requested-With':'SouthboundJobs'})
                    with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
                    self.assertEqual(e.exception.code,403)
                with urllib.request.urlopen(base+'/api/status') as r: status=json.load(r)
                self.assertEqual(status['mode'],'server');self.assertFalse(status['canRefresh'])
                with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(base+'/logos/')
                self.assertEqual(e.exception.code,404)
            finally: http.shutdown();http.server_close();thread.join()

class ScheduledRetryTests(unittest.TestCase):
    def test_retries_only_failed_sources_once(self):
        from deploy.refresh import run_sync
        from unittest.mock import Mock
        first={'sources':[{'id':'google','status':'error'},{'id':'amazon','status':'ok'}]}
        recovered={'sources':[{'id':'google','status':'ok'},{'id':'amazon','status':'ok'}]}
        fn=Mock(side_effect=[first,recovered]);pause=Mock()
        self.assertEqual(run_sync(fn,pause),0)
        self.assertEqual(fn.call_args_list[1].args,(['google'],))
        pause.assert_called_once_with(5)

    def test_continuing_failures_remain_visible(self):
        from deploy.refresh import run_sync
        from unittest.mock import Mock
        failed={'sources':[{'id':'google','status':'error'}]}
        fn=Mock(return_value=failed)
        self.assertEqual(run_sync(fn,Mock()),1)
        self.assertEqual(fn.call_count,2)

if __name__=='__main__': unittest.main()
