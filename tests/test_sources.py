import json
import unittest
from collector import make_job, cities_for, plain
from source_adapters import PublicSources, apple_search, check_page, google_search


class SourceTests(unittest.TestCase):
    def adapter(self, request):
        return PublicSources(request, make_job, cities_for, plain)

    def test_airwallex_secondary_location_and_unlisted(self):
        rows = [dict(id='1', title='Software Engineer', location='London',
                     secondaryLocations=[{'location': 'HK - Hong Kong SAR'}],
                     jobUrl='https://jobs.ashbyhq.com/airwallex/1', isListed=True),
                dict(id='2', title='Singapore sales team', location='London',
                     jobUrl='https://jobs.ashbyhq.com/airwallex/2'),
                dict(id='3', title='Engineer', location='Singapore', isListed=False)]
        jobs = self.adapter(lambda *_: json.dumps({'jobs': rows})).airwallex()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['cities'], ['香港'])
        self.assertNotIn('/application', jobs[0]['url'])

    def test_apple_rejects_unrecognized_location_and_login_page(self):
        data = {'loaderData': {'search': {'filters': {'locations': [{'code': 'SGP'}]}}}}
        page = 'window.__staticRouterHydrationData = JSON.parse(' + json.dumps(json.dumps(data)) + ');'
        self.assertEqual(apple_search(page, 'singapore-SGP'), data['loaderData']['search'])
        with self.assertRaises(ValueError): apple_search(page, 'guangzhou-GNG')
        with self.assertRaises(ValueError): apple_search('<html>Sign in</html>', 'singapore-SGP')

    def test_pagination_cannot_silently_truncate_or_repeat(self):
        with self.assertRaises(ValueError): check_page([], 21, 20, set(), set(), 'test')
        with self.assertRaises(ValueError): check_page([1], 21, 20, {'id'}, {'id'}, 'test')
        check_page([], 0, 0, set(), set(), 'test')

    def test_google_full_jd_url_and_unknown_date(self):
        row = ['123', 'Software Engineer', 'https://example.com/signin', None, None,
               None, None, 'Google', 'en', [['Singapore']], [None, '<p>About the job</p>']]
        row.extend([None] * 5)
        def request(*_):
            return "AF_initDataCallback({key: 'ds:1',data:" + json.dumps([[row], None, 1, 20]) + '});'
        jobs = self.adapter(request).google()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['url'], 'https://www.google.com/about/careers/applications/jobs/results/123')
        self.assertIsNone(jobs[0]['postedAt'])
        self.assertEqual(google_search("AF_initDataCallback({key: 'ds:1',data:[null,null,0,20]});")[0], [])

    def test_meta_ignores_featured_and_false_location_matches(self):
        def request(url, **kwargs):
            if url.endswith('/jobsearch/'):
                return '["LSD",[],{"token":"public-test-token"}]'
            self.assertEqual(kwargs['extra_headers']['X-FB-LSD'], 'public-test-token')
            return json.dumps({'data': {'job_search_with_featured_jobs_v2': {
                'all_jobs': [dict(id='1', title='Engineer', locations=['Singapore']),
                             dict(id='2', title='Singapore team', locations=['Seattle'])],
                'featured_jobs': [dict(id='3', title='Unrelated', locations=['Singapore'])]}}})
        jobs = self.adapter(request).meta()
        self.assertEqual([j['id'] for j in jobs], ['meta:1'])
        self.assertEqual(jobs[0]['url'], 'https://www.metacareers.com/profile/job_details/1/')

    def test_workday_reads_actual_multiple_locations_and_uses_facet(self):
        def request(url, body=None):
            if not url.endswith('/jobs'):
                return json.dumps({'jobPostingInfo': dict(jobReqId='JR1', title='Engineer',
                    location='London', additionalLocations=['Singapore, Remote'],
                    externalUrl='https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/JR1',
                    startDate='2026-09-01', posted=True, canApply=True)})
            if not body['appliedFacets']:
                return json.dumps({'facets': [{'facetParameter': 'locationMainGroup', 'values': [
                    {'facetParameter': 'locations', 'values': [
                        {'descriptor': 'Singapore, Remote', 'id': 'sg'},
                        {'descriptor': 'London', 'id': 'uk'}]}]}]})
            self.assertEqual(body['appliedFacets'], {'locations': ['sg']})
            return json.dumps({'total': 1, 'jobPostings': [{'externalPath': '/job/JR1'}]})
        jobs = self.adapter(request).nvidia()
        self.assertEqual(jobs[0]['cities'], ['新加坡'])
        self.assertEqual(jobs[0]['postedAt'], '2026-09-01')

    def test_workday_later_pages_report_zero_total(self):
        offsets = []
        def request(url, body=None):
            if not url.endswith('/jobs'):
                return json.dumps({'jobPostingInfo': dict(jobReqId=url.rsplit('/', 1)[-1], title='Engineer',
                    location='Singapore', additionalLocations=None, posted=True, canApply=True)})
            if not body['appliedFacets']:
                return json.dumps({'facets': [{'facetParameter': 'locations', 'values': [
                    {'descriptor': 'Singapore', 'id': 'sg'}]}]})
            offset = body['offset']
            offsets.append(offset)
            return json.dumps({'total': 3 if offset == 0 else 0,
                'jobPostings': [{'externalPath': '/job/' + str(offset)}]})
        jobs = self.adapter(request).nvidia()
        self.assertEqual(offsets, [0, 1, 2])
        self.assertEqual(len(jobs), 3)


if __name__ == '__main__': unittest.main()
