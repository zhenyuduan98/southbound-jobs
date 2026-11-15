import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from collector import merge, make_job, parse_walmart, cities_for, category, process_lock, is_running
from server import should_refresh

T1 = '2026-09-28T09:00:00+08:00'
T2 = '2026-09-29T09:00:00+08:00'

def job(id='1', company='microsoft'):
    return make_job(company, id, 'Software Engineer', ['Singapore, Singapore'], 'https://example.com/job/1')

class CollectorTests(unittest.TestCase):
    def test_first_sync_is_baseline_not_new_posting(self):
        d = merge({}, {'microsoft': [job()]}, T1)
        self.assertTrue(d['jobs'][0]['baseline'])
        self.assertIsNone(d['jobs'][0]['postedAt'])

    def test_failure_preserves_last_seen_and_marks_stale(self):
        d = merge({}, {'microsoft': [job()]}, T1)
        d = merge(d, {'microsoft': RuntimeError('429 rate limit')}, T2)
        self.assertEqual(d['jobs'][0]['status'], 'stale')
        self.assertEqual(d['jobs'][0]['lastSeenAt'], T1)
        self.assertEqual(d['sources'][0]['lastSuccessAt'], T1)

    def test_success_missing_and_reappeared(self):
        d = merge({}, {'microsoft': [job()]}, T1)
        d = merge(d, {'microsoft': []}, T2)
        self.assertEqual(d['jobs'][0]['status'], 'missing')
        d = merge(d, {'microsoft': [job()]}, T2)
        self.assertEqual(d['jobs'][0]['status'], 'active')
        self.assertEqual(d['jobs'][0]['firstSeenAt'], T1)
        self.assertNotIn('missingSince', d['jobs'][0])

    def test_new_job_and_no_duplicate(self):
        d = merge({}, {'microsoft': [job()]}, T1)
        d = merge(d, {'microsoft': [job(), job(), job('2')]}, T2)
        self.assertEqual(len(d['jobs']), 2)
        self.assertEqual(d['history'][0]['added'], 1)
        self.assertFalse(next(j for j in d['jobs'] if j['id'].endswith(':2'))['baseline'])

    def test_initial_source_failure_has_baseline_on_recovery(self):
        d = merge({}, {'microsoft': RuntimeError('offline'), 'amazon': [job(company='amazon')]}, T1)
        d = merge(d, {'microsoft': [job()], 'amazon': [job(company='amazon')]}, T2)
        self.assertTrue(next(j for j in d['jobs'] if j['company']=='microsoft')['baseline'])

    def test_locations_are_actual_locations_not_search_terms(self):
        self.assertEqual(cities_for(['Shenzhen, China', 'Hong Kong']), ['深圳', '香港'])
        self.assertIsNone(make_job('amazon','2','Singapore sales team',['Seattle'],'https://example.com'))

    def test_walmart_parser_and_pagination(self):
        fixture = '''<article class="article article--result"><h3 class="title"><a href="https://walmartchina.avature.cn/zh_CN/careers/JobDetail/123">科技部&amp;产品部</a></h3><span class="article--result_location">深圳市</span><span class="article--result_created">09/11/26</span></article><a href="?jobOffset=20">3</a>'''
        jobs, last = parse_walmart(fixture)
        self.assertEqual(jobs[0]['title'], '科技部&产品部')
        self.assertEqual(jobs[0]['cities'], ['深圳'])
        self.assertEqual(last, 20)
        with self.assertRaises(ValueError): parse_walmart('<html>verification required</html>')

    def test_failed_source_does_not_expire_other_source(self):
        d = merge({}, {'microsoft': [job()], 'amazon': [job(company='amazon')]}, T1)
        d = merge(d, {'microsoft': [], 'amazon': RuntimeError('offline')}, T2)
        states = {j['company']: j['status'] for j in d['jobs']}
        self.assertEqual(states, {'microsoft':'missing','amazon':'stale'})

    def test_targeted_refresh_preserves_other_source_status(self):
        d = merge({}, {'microsoft': [job()], 'amazon': [job(company='amazon')]}, T1)
        before = next(s for s in d['sources'] if s['id'] == 'amazon')
        d = merge(d, {'microsoft': [job()]}, T2)
        self.assertEqual(next(s for s in d['sources'] if s['id'] == 'amazon'), before)
        self.assertEqual(d['history'][0]['total'], 1)

    def test_daily_schedule_and_catchup(self):
        self.assertTrue(should_refresh(None, datetime.fromisoformat('2026-09-29T08:00:00+08:00')))
        self.assertFalse(should_refresh(T1, datetime.fromisoformat('2026-09-29T08:59:00+08:00')))
        self.assertTrue(should_refresh(T1, datetime.fromisoformat(T2)))
        self.assertFalse(should_refresh(T2, datetime.fromisoformat('2026-09-29T18:00:00+08:00')))
        self.assertTrue(should_refresh(T1, datetime.fromisoformat('2026-09-30T12:00:00+08:00')))

    def test_status_probe_while_another_process_holds_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch('collector.ROOT', Path(directory)):
            held = process_lock()
            self.assertIsNotNone(held)
            try:
                self.assertTrue(is_running())
                self.assertIsNone(process_lock())
            finally:
                held.close()
            self.assertFalse(is_running())

if __name__ == '__main__': unittest.main()
