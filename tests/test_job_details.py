import json
import tempfile
import unittest
from pathlib import Path
from job_details import DetailStore, clean_text, parse_page, detail_path

JOB = {'id':'apple:123', 'title':'Engineer', 'company':'apple', 'url':'https://jobs.apple.com/en-us/details/123'}


class DetailTests(unittest.TestCase):
    def test_text_preserves_lists_and_removes_active_content(self):
        text = clean_text('<h2>Requirements</h2><ul><li>Python &amp; SQL</li><li>Experience</li></ul><script>bad()</script><style>hide</style>')
        self.assertIn('Requirements\n', text)
        self.assertIn('• Python & SQL', text)
        self.assertNotIn('bad', text)
        self.assertNotIn('hide', text)

    def test_apple_keeps_long_description_requirements_and_footer(self):
        body = 'Full original description. ' * 100
        x = {'jobSummary':'Summary', 'description':body, 'minimumQualifications':'Skills',
             'preferredQualifications':'Preferred skills', 'postingFooters':[{'localizations':{'en_US':[{'name':'EEO','content':'Equal opportunity.'}]}}]}
        source = 'window.__staticRouterHydrationData = JSON.parse(' + json.dumps(json.dumps({'loaderData':{'jobDetails':{'jobsData':x}}})) + ');'
        items = parse_page('apple', source)
        self.assertEqual(next(s['text'] for s in items if s['heading']=='Description'), body.strip())
        self.assertEqual([s['heading'] for s in items], ['Summary','Description','Minimum Qualifications','Preferred Qualifications','EEO'])

    def test_amazon_extracts_all_three_sections_without_site_navigation(self):
        source = '<nav>Unrelated</nav>' + ''.join('<div class="section"><h2>'+heading+'</h2><p>'+body+'</p></div>' for heading,body in [('Description','Full role'),('Basic Qualifications','Skill A'),('Preferred Qualifications','Skill B')])
        items = parse_page('amazon', source)
        self.assertEqual(len(items), 3)
        self.assertIn('Skill B', items[-1]['text'])
        self.assertNotIn('Unrelated', str(items))

    def test_google_keeps_qualifications_about_and_responsibilities(self):
        source = '<div>Apply button<h3>Minimum qualifications:</h3><ul><li>A</li></ul><h3>Preferred qualifications:</h3><p>B</p></div><div class="aG5W3"><h3>About the job</h3>C</div><div class="BDNOWe"><h3>Responsibilities</h3>D</div>'
        items = parse_page('google', source)
        self.assertEqual(len(items), 3)
        self.assertIn('Preferred qualifications', items[0]['text'])
        self.assertNotIn('Apply button', items[0]['text'])

    def test_cache_and_failure_keep_original_body(self):
        with tempfile.TemporaryDirectory() as root:
            store = DetailStore(lambda *_: None, root)
            content = [{'heading':'Description', 'text':'Full body ' * 30}]
            store.save(JOB, content)
            store.fetch = lambda _: self.fail('Fresh cache should not refetch')
            self.assertEqual(store.get(JOB)['sections'], content)
            path = Path(root)/'public'/detail_path(JOB)
            cached = json.loads(path.read_text(encoding='utf-8'))
            cached['fetchedAt']='2020-01-01T00:00:00+08:00'
            path.write_text(json.dumps(cached), encoding='utf-8')
            def fail(_): raise ValueError('offline')
            store.fetch = fail
            result = store.get(JOB)
            self.assertTrue(result['stale'])
            self.assertEqual(result['sections'], content)
            self.assertEqual(result['fetchedAt'], cached['fetchedAt'])

    def test_goldman_empty_description_uses_linked_official_recruitment_site(self):
        role = {'descriptionHtml':None, 'externalSource':{'sourceId':'42','externalApplicationUrl':'https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/42/apply/email'}}
        def request(url):
            if 'higher.gs.com' in url:
                return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'role':role}}})+'</script>'
            return json.dumps({'items':[{'Id':'42','ExternalDescriptionStr':'<br>', 'CorporateDescriptionStr':'Program details', 'OrganizationDescriptionStr':'Division details', 'InternalQualificationsStr':'Not a public JD'}]})
        result = DetailStore(request).fetch({'id':'goldman:42','company':'goldman','url':'https://higher.gs.com/roles/42'})
        self.assertEqual([x['text'] for x in result], ['Program details','Division details'])


if __name__ == '__main__': unittest.main()
