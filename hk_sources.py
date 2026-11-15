"""Hong Kong institutions and enterprise-software employers; public endpoints only."""
import concurrent.futures
import json
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from job_details import Document, sections, normalize_text

TZ = timezone(timedelta(hours=8))
HSBC_API = 'https://portal.careers.hsbc.com/api'
HKJC_HOST = 'https://careers.hkjc.com'
SC_HOST = 'https://jobs.standardchartered.com'
ORACLE_HOST = 'https://eeho.fa.us2.oraclecloud.com'
ORACLE_SITE = 'CX_45001'
XML_FEEDS = {'sap': 'https://jobs.sap.com/en/jobs/xml/?rss=true',
             'servicenow': 'https://careers.servicenow.com/jobs/xml/?rss=true'}
FEED_CACHE = {}
FEED_LOCK = threading.Lock()
JANE_FEED = 'https://www.janestreet.com/jobs/main.json'
JANE_CITIES = {'HKG': 'Hong Kong', 'SGP': 'Singapore', 'NYC': 'New York',
               'LDN': 'London', 'ATX': 'Austin', 'CHI': 'Chicago'}
JANE_KINDS = {'Summer Internship': '实习 / 校招', 'Full-Time: New Grad': '实习 / 校招'}
JANE_CACHE = {}
JANE_LOCK = threading.Lock()


def node_text(source, predicate, label):
    """Read one official description container without re-parsing its text as HTML."""
    nodes = Document(source).root.find(predicate)
    body = normalize_text('\n'.join(n.text() for n in nodes))
    if not body:
        raise ValueError(label + ' JD unavailable')
    return [{'heading': '', 'text': body}]


def hsbc_sections(request, id):
    qs = urllib.parse.urlencode({'position_id': id, 'domain': 'hsbc.com'})
    x = json.loads(request(HSBC_API + '/pcsx/position_details?' + qs))['data']
    return sections(('', x['jobDescription']))


def hkjc_sections(request, url):
    return node_text(request(url),
        lambda n: 'jobdescription' in n.attrs.get('class', '').split(), 'HKJC')


def standardchartered_sections(request, url):
    return node_text(request(url),
        lambda n: n.attrs.get('itemprop') == 'description', 'Standard Chartered')


def oracle_sections(request, id):
    qs = urllib.parse.urlencode({'onlyData': 'true', 'expand': 'all',
        'finder': f'ById;Id="{id}",siteNumber="{ORACLE_SITE}"'})
    rows = json.loads(request(ORACLE_HOST + '/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?' + qs))['items']
    x = next(x for x in rows if str(x['Id']) == str(id))
    return sections(('Description', x.get('ExternalDescriptionStr')),
        ('About the company', x.get('CorporateDescriptionStr')),
        ('About the organization', x.get('OrganizationDescriptionStr')),
        ('Responsibilities', x.get('ExternalResponsibilitiesStr')),
        ('Qualifications', x.get('ExternalQualificationsStr')))


def feed_fields(block):
    fields = {k: v for k, v in re.findall(r'<(\w+)><!\[CDATA\[(.*?)\]\]></\1>', block, re.S)}
    for k, v in re.findall(r'<(\w+)>([^<]*)</\1>', block):
        fields.setdefault(k, v)
    return fields


def feed_rows(request, company):
    """One daily download of the official XML feed serves both listing and JD text."""
    today = datetime.now(TZ).date().isoformat()
    with FEED_LOCK:
        day, rows = FEED_CACHE.get(company, (None, {}))
        if day == today:
            return rows
    blocks = re.findall(r'<job>(.*?)</job>', request(XML_FEEDS[company]), re.S)
    if not blocks:
        raise ValueError(company + ' official XML feed unavailable')
    rows = {}
    for block in blocks:
        f = feed_fields(block)
        id = f.get('requisitionid') or f.get('apijobid') or f.get('referencenumber')
        if id and f.get('url'):
            rows[str(id)] = f
    with FEED_LOCK:
        FEED_CACHE[company] = (today, rows)
    return rows


def feed_sections(request, company, id):
    row = feed_rows(request, company).get(str(id))
    if not row:
        raise ValueError(company + ' job is no longer in the official feed')
    return sections(('', row.get('description')))


def jane_rows(request):
    """One daily download of the official job feed serves both listing and JD text."""
    today = datetime.now(TZ).date().isoformat()
    with JANE_LOCK:
        day, rows = JANE_CACHE.get('janestreet', (None, {}))
        if day == today:
            return rows
    data = json.loads(request(JANE_FEED))
    if not isinstance(data, list) or not data:
        raise ValueError('Jane Street official job feed unavailable')
    rows = {str(x['id']): x for x in data if x.get('id') and x.get('position')}
    if not rows:
        raise ValueError('Jane Street official job feed returned no jobs')
    with JANE_LOCK:
        JANE_CACHE['janestreet'] = (today, rows)
    return rows


def jane_sections(request, id):
    row = jane_rows(request).get(str(id))
    if not row:
        raise ValueError('Jane Street job is no longer in the official feed')
    return sections(('', row.get('overview')))


class ExtraSources:
    def __init__(self, request, make_job, cities_for, plain):
        self.request, self.make_job = request, make_job
        self.cities_for, self.plain = cities_for, plain

    def hsbc(self):
        from source_adapters import check_page
        out = {}
        # Each official catchment overlaps its neighbours; results are merged by id.
        for city in ('Hong Kong', 'Singapore', 'Guangzhou'):
            seen, processed, total = set(), 0, None
            while True:
                qs = urllib.parse.urlencode({'domain': 'hsbc.com', 'location': city, 'start': processed, 'num': 100})
                data = json.loads(self.request(HSBC_API + '/apply/v2/jobs?' + qs))
                rows = data['positions']
                if total is None: total = data['count']
                check_page(rows, total, processed, seen, {str(x['id']) for x in rows}, 'HSBC')
                if not rows: break
                for x in rows:
                    posted = datetime.fromtimestamp(x['t_create'], TZ).isoformat() if x.get('t_create') else None
                    locs = list(dict.fromkeys(filter(None, [x.get('location')] + (x.get('locations') or []))))
                    j = self.make_job('hsbc', x['id'], x['name'], locs,
                        x.get('canonicalPositionUrl') or f"https://portal.careers.hsbc.com/careers/job/{x['id']}",
                        x.get('department') or x.get('business_unit') or '', posted)
                    if j: out[j['id']] = j
                processed += len(rows)
                if processed >= total: break
                if processed > 3000: raise ValueError('HSBC pagination limit')
                time.sleep(.2)
        return list(out.values())

    def hkjc(self):
        out, startrow, total = {}, 0, None
        while True:
            page = self.request(f'{HKJC_HOST}/search/?q=&startrow={startrow}')
            if total is None:
                match = re.search(r'Results \d+ to \d+ of ([\d,]+)', page)
                if not match: raise ValueError('HKJC result count unavailable')
                total = int(match[1].replace(',', ''))
            rows = re.findall(r'<tr class="data-row">(.*?)</tr>', page, re.S)
            if not rows: raise ValueError('HKJC result rows unavailable')
            for row in rows:
                link = re.search(r'<a href="(/job/[^"]+)"[^>]*class="jobTitle-link"[^>]*>(.*?)</a>', row, re.S)
                if not link: raise ValueError('HKJC row missing job link')
                division = re.search(r'<td class="colFacility[^"]*"[^>]*>\s*<span class="jobFacility">(.*?)</span>', row, re.S)
                location = re.search(r'<td class="colLocation[^"]*"[^>]*>\s*<span class="jobLocation">(.*?)</span>', row, re.S)
                date = re.search(r'<span class="jobDate">(.*?)</span>', row, re.S)
                posted = None
                if date:
                    try: posted = datetime.strptime(self.plain(date[1]), '%d %b %Y').replace(tzinfo=TZ).isoformat()
                    except ValueError: posted = None
                url = urllib.parse.urljoin(HKJC_HOST, link[1])
                place = self.plain(location[1]) if location else ''
                # The official listing writes Hong Kong districts with the country code only.
                locs = [place] + (['Hong Kong'] if place.split(',')[-1].strip() == 'HK' else [])
                j = self.make_job('hkjc', url.rstrip('/').rsplit('/', 1)[-1], link[2],
                    locs, url, self.plain(division[1]) if division else '', posted)
                if j: out[j['id']] = j
            startrow += len(rows)
            if startrow >= total: break
            if startrow > 2000: raise ValueError('HKJC pagination limit')
            time.sleep(.2)
        return list(out.values())

    def standardchartered(self):
        out = {}
        # The public API returns ten rows per page, filters by country only and
        # shuffles its result order, so each country is swept repeatedly until
        # the merged set reaches the reported total or stops growing.
        for country in ('Hong Kong', 'Singapore', 'China'):
            seen, total = set(), None
            for sweep in range(0, 6):
                before = len(seen)
                page = 0
                while page < 200:
                    data = json.loads(self.request(SC_HOST + '/services/recruiting/v1/jobs', {
                        'locale': 'en_GB', 'pageNumber': page, 'sortBy': '', 'keywords': '', 'location': '',
                        'facetFilters': {'jobLocationCountry': [country]}, 'brand': '', 'skills': [],
                        'categoryId': 0, 'alertId': '', 'rcmCandidateId': ''}))
                    if 'jobSearchResult' not in data or 'totalJobs' not in data:
                        raise ValueError('Standard Chartered search response changed')
                    rows = [r['response'] for r in data['jobSearchResult']]
                    if total is None: total = data['totalJobs']
                    for x in rows:
                        seen.add(x['id'])
                        posted = None
                        if x.get('unifiedStandardStart'):
                            try: posted = datetime.strptime(x['unifiedStandardStart'], '%d/%m/%Y').replace(tzinfo=TZ).isoformat()
                            except ValueError: posted = None
                        slug = x.get('unifiedUrlTitle') or x.get('urlTitle') or 'job'
                        j = self.make_job('standardchartered', x['id'],
                            x.get('unifiedStandardTitle') or x.get('urlTitle'),
                            list(x.get('jobLocationShort') or []) + list(x.get('jobLocationCountry') or []),
                            f"{SC_HOST}/job/{slug}/{x['id']}-en_GB",
                            ' / '.join(x.get('mfield1') or []), posted)
                        if j: out[j['id']] = j
                    page += 1
                    if not rows or page * 10 >= total: break
                    time.sleep(.15)
                else: raise ValueError('Standard Chartered pagination limit')
                if len(seen) >= total or len(seen) == before: break
            if total and not seen: raise ValueError('Standard Chartered returned no jobs')
        return list(out.values())

    def oracle(self):
        from source_adapters import check_page
        out = {}
        for city in ('Shenzhen', 'Hong Kong', 'Singapore', 'Guangzhou'):
            seen, processed, total = set(), 0, None
            while True:
                qs = urllib.parse.urlencode({'onlyData': 'true',
                    'expand': 'requisitionList.secondaryLocations',
                    'finder': f'findReqs;siteNumber={ORACLE_SITE},limit=100,offset={processed},'
                              f'keyword="{city}",sortBy=POSTING_DATES_DESC'})
                data = json.loads(self.request(ORACLE_HOST + '/hcmRestApi/resources/latest/recruitingCEJobRequisitions?' + qs))['items'][0]
                rows = data['requisitionList']
                if total is None: total = data['TotalJobsCount']
                check_page(rows, total, processed, seen, {str(x['Id']) for x in rows}, 'Oracle')
                for x in rows:
                    locs = [x.get('PrimaryLocation') or '']
                    locs += [l.get('Name') or '' for l in (x.get('secondaryLocations') or [])]
                    j = self.make_job('oracle', x['Id'], x['Title'], locs,
                        f"https://careers.oracle.com/jobs/#en/sites/jobsearch/job/{x['Id']}",
                        x.get('JobFunction') or x.get('Department') or '', x.get('PostedDate'))
                    if j: out[j['id']] = j
                processed += len(rows)
                if processed >= total or not rows: break
                if processed > 2000: raise ValueError('Oracle pagination limit')
                time.sleep(.2)
        return list(out.values())

    def xml_feed(self, company):
        out = {}
        for id, f in feed_rows(self.request, company).items():
            locs = [', '.join(x for x in [f.get('city'), f.get('state'), f.get('country')] if x)]
            j = self.make_job(company, id, f.get('title', ''), locs, f['url'],
                f.get('department') or f.get('category') or '', f.get('date') or None,
                self.plain(f.get('description', ''))[:1000])
            if j:
                j['jdSections'] = sections(('', f.get('description')))
                out[j['id']] = j
        return list(out.values())

    def sap(self):
        return self.xml_feed('sap')

    def servicenow(self):
        return self.xml_feed('servicenow')

    def janestreet(self):
        out = {}
        for id, x in jane_rows(self.request).items():
            city = x.get('city') or ''
            locs = [JANE_CITIES.get(city, city)]
            j = self.make_job('janestreet', id, x.get('position', ''), locs,
                f'https://www.janestreet.com/join-jane-street/position/{id}/',
                x.get('team') or x.get('category') or '', None,
                self.plain(x.get('overview', ''))[:1000],
                JANE_KINDS.get(x.get('availability')))
            if j:
                j['jdSections'] = sections(('', x.get('overview')))
                out[j['id']] = j
        return list(out.values())
