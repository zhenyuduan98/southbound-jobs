"""Additional public employer adapters; no applicant login or API keys required."""
import concurrent.futures
import json
import re
import time
import urllib.parse
from job_details import sections

CITIES = ('Shenzhen', 'Hong Kong', 'Singapore', 'Guangzhou')


def apple_search(document, location):
    marker = 'window.__staticRouterHydrationData = JSON.parse('
    if marker not in document:
        raise ValueError('Apple search data unavailable')
    encoded, _ = json.JSONDecoder().raw_decode(document.split(marker, 1)[1])
    data = json.loads(encoded)['loaderData']['search']
    code = location.rsplit('-', 1)[-1]
    if not any(x['code'] == code for x in data['filters'].get('locations', [])):
        raise ValueError('Apple location filter not accepted: ' + location)
    return data


def google_search(document):
    match = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:", document)
    if not match:
        raise ValueError('Google search data unavailable')
    data, _ = json.JSONDecoder().raw_decode(document[match.end():])
    if not isinstance(data, list) or len(data) < 4 or not isinstance(data[2], int):
        raise ValueError('Google search format changed')
    # The official page uses null for no results.
    if data[0] is None and data[2] == 0:
        data[0] = []
    return data


def check_page(rows, total, processed, seen, ids, source):
    if not rows and processed < total:
        raise ValueError(source + ' pagination ended before count')
    if rows and ids <= seen:
        raise ValueError(source + ' pagination repeated')
    seen.update(ids)


class PublicSources:
    def __init__(self, request, make_job, cities_for, plain):
        self.request, self.make_job = request, make_job
        self.cities_for, self.plain = cities_for, plain

    def workday(self, company, tenant, site, wd='wd5', facet='locations', countries=None):
        base = f'https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}'
        def search(offset=0, facets=None):
            return json.loads(self.request(base + '/jobs', {
                'limit': 20, 'offset': offset, 'searchText': '', 'appliedFacets': facets or {}}))
        initial = search()
        locations = []
        found_facet = False
        def wanted(descriptor):
            if countries is not None:
                return descriptor in countries
            return bool(self.cities_for([descriptor]))
        def visit(facets):
            nonlocal found_facet
            for group in facets:
                if group.get('facetParameter') == facet:
                    found_facet = True
                    locations.extend(x['id'] for x in group['values'] if wanted(x['descriptor']))
                else:
                    visit([x for x in group.get('values', []) if 'facetParameter' in x])
        visit(initial['facets'])
        if not found_facet:
            raise ValueError(company + ' location facets unavailable')
        if not locations:
            return []
        paths, seen, offset, total = [], set(), 0, None
        while True:
            data = search(offset, {facet: list(dict.fromkeys(locations))})
            rows = data['jobPostings']
            # Workday returns total=0 on later pages even when more jobs remain.
            if total is None: total = data['total']
            ids = {x['externalPath'] for x in rows}
            check_page(rows, total, offset, seen, ids, company)
            paths.extend(x['externalPath'] for x in rows)
            offset += len(rows)
            if offset >= total:
                break
            if offset > 3000:
                raise ValueError(company + ' pagination limit')
            time.sleep(.15)
        def detail(path):
            x = json.loads(self.request(base + path))['jobPostingInfo']
            if x.get('posted') is False or x.get('canApply') is False:
                return None
            # Some tenants only publish a floor name, with the city in the country facet.
            locs = [x.get('location') or ''] + (x.get('additionalLocations') or [])
            country = (x.get('country') or {}).get('descriptor', '')
            if country: locs.append(country)
            job = self.make_job(company, x['jobReqId'], x['title'], locs,
                x.get('externalUrl') or f'https://{tenant}.{wd}.myworkdayjobs.com/{site}' + path,
                posted=x.get('startDate'), summary=self.plain(x.get('jobDescription', ''))[:1000])
            if job: job['jdSections'] = sections(('', x.get('jobDescription')))
            return job
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            jobs = list(pool.map(detail, dict.fromkeys(paths)))
        return [j for j in jobs if j]

    def nvidia(self):
        return self.workday('nvidia', 'nvidia', 'NVIDIAExternalCareerSite')

    def cisco(self):
        return self.workday('cisco', 'cisco', 'Cisco_Careers')

    def airwallex(self):
        data = json.loads(self.request('https://api.ashbyhq.com/posting-api/job-board/airwallex'))
        out = []
        for x in data['jobs']:
            if not x.get('isListed', True):
                continue
            locs = [x['location']] + [l['location'] for l in x.get('secondaryLocations', [])]
            j = self.make_job('airwallex', x['id'], x['title'], locs, x['jobUrl'],
                ' / '.join(filter(None, [x.get('department'), x.get('team')])),
                x.get('publishedAt'), self.plain(x.get('descriptionPlain', ''))[:1000])
            if j:
                j['jdSections'] = sections(('', x.get('descriptionHtml') or x.get('descriptionPlain')))
                out.append(j)
        return out

    def apple(self):
        out = {}
        for location in ('shenzhen-SHZ', 'hong-kong-HKG', 'singapore-SGP', 'guangzhou-GNG'):
            seen, processed = set(), 0
            for page in range(1, 101):
                qs = urllib.parse.urlencode({'location': location, 'page': page})
                data = apple_search(self.request('https://jobs.apple.com/en-us/search?' + qs), location)
                rows, total = data['searchResults'], data['totalRecords']
                check_page(rows, total, processed, seen, {x['id'] for x in rows}, 'Apple')
                for x in rows:
                    if x.get('postExternal') is False: continue
                    locs = [', '.join(dict.fromkeys(filter(None, [l.get('name'), l.get('city'), l.get('countryName')]))) for l in x['locations']]
                    id = x['positionId'] if x['id'].startswith('PIPE-') else x['id']
                    url = f"https://jobs.apple.com/en-us/details/{id}/{x['transformedPostingTitle']}"
                    j = self.make_job('apple', x['id'], x['postingTitle'], locs, url,
                        (x.get('team') or {}).get('teamName', ''), x.get('postDateInGMT'),
                        self.plain(x.get('jobSummary', ''))[:1000])
                    if j: out[j['id']] = j
                processed += len(rows)
                if processed >= total: break
                time.sleep(.15)
            else: raise ValueError('Apple pagination limit')
        return list(out.values())

    def google(self):
        out = {}
        for city in CITIES:
            seen, processed = set(), 0
            for page in range(1, 101):
                qs = urllib.parse.urlencode({'location': city, 'page': page})
                data = google_search(self.request('https://www.google.com/about/careers/applications/jobs/results/?' + qs))
                rows, total = data[0], data[2]
                check_page(rows, total, processed, seen, {x[0] for x in rows}, 'Google')
                for x in rows:
                    locs = [l[0] for l in (x[9] or [])]
                    j = self.make_job('google', x[0], x[1], locs,
                        'https://www.google.com/about/careers/applications/jobs/results/' + x[0],
                        x[7] or '', summary=self.plain((x[10] or [None, ''])[1])[:1000])
                    if j:
                        j['jdSections'] = sections(('', (x[15] or [None, ''])[1]), ('Qualifications', (x[4] or [None, ''])[1]), ('About the job', (x[10] or [None, ''])[1]), ('Responsibilities', (x[3] or [None, ''])[1]))
                        out[j['id']] = j
                processed += len(rows)
                if processed >= total: break
                time.sleep(.15)
            else: raise ValueError('Google pagination limit')
        return list(out.values())

    def meta(self):
        document = self.request('https://www.metacareers.com/jobsearch/')
        match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"', document)
        if not match:
            raise ValueError('Meta public search token unavailable')
        token, out = match[1], {}
        for city in CITIES:
            variables = {'search_input': {'q': city, 'divisions': [], 'offices': [], 'roles': [],
                'leadership_levels': [], 'saved_jobs': [], 'saved_searches': [], 'sub_teams': [], 'teams': [],
                'is_leadership': False, 'is_remote_only': False, 'sort_by_new': False, 'results_per_page': None},
                'viewasUserID': None, 'isLoggedIn': False}
            data = json.loads(self.request('https://www.metacareers.com/graphql', form={
                'lsd': token, '__a': '1', '__user': '0', 'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'CareersJobSearchResultsV2DataQuery',
                'doc_id': '27129360303422352', 'variables': json.dumps(variables)}, extra_headers={'X-FB-LSD': token}))
            if data.get('errors'):
                raise ValueError('Meta public search failed; adapter needs review')
            rows = data['data']['job_search_with_featured_jobs_v2']['all_jobs']
            if not isinstance(rows, list):
                raise ValueError('Meta search format changed')
            for x in rows:
                j = self.make_job('meta', x['id'], x['title'], x['locations'],
                    'https://www.metacareers.com/profile/job_details/' + x['id'] + '/')
                if j: out[j['id']] = j
            time.sleep(.15)
        return list(out.values())
