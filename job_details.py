"""Full official JD text, cached separately from the lightweight job index."""
import concurrent.futures
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit, urlencode
from jd_translation import translation_info

ROOT = Path(os.environ.get('SOUTHBOUND_DATA_ROOT', Path(__file__).resolve().parent))
TZ = timezone(timedelta(hours=8))
VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'col'}
BLOCK = {'p', 'div', 'section', 'article', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'tr', 'br', 'hr'}
# Official Workday tenants: company -> (tenant, career site, Workday cluster).
WORKDAY = {'nvidia': ('nvidia', 'NVIDIAExternalCareerSite', 'wd5'),
           'cisco': ('cisco', 'Cisco_Careers', 'wd5'),
           'aia': ('aia', 'External', 'wd3'),
           'hkex': ('hkex', 'HKEXCareerPage', 'wd3'),
           'salesforce': ('salesforce', 'External_Career_Site', 'wd12')}


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        if self.tag in {'script', 'style', 'noscript', 'iframe', 'svg'}: return ''
        if self.attrs.get('aria-hidden') == 'true': return ''
        content = ''.join(x.text() if isinstance(x, Node) else x for x in self.children)
        if self.tag == 'li': content = '• ' + content.strip()
        return '\n' + content + '\n' if self.tag in BLOCK else content

    def find(self, predicate):
        return ([self] if predicate(self) else []) + [n for c in self.children if isinstance(c, Node) for n in c.find(predicate)]


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID: self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def clean_text(value):
    if value is None: return ''
    if isinstance(value, list): value = '\n'.join(str(x) for x in value)
    return normalize_text(Document(str(value)).root.text())


def normalize_text(text):
    text = text.replace('\xa0', ' ')
    text = '\n'.join(re.sub(r'[ \t\r\f\v]+', ' ', line).strip() for line in text.split('\n'))
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def sections(*items):
    return [{'heading': heading, 'text': clean_text(value)} for heading, value in items if clean_text(value)]


def detail_path(job):
    return 'details/' + hashlib.sha256(job['id'].encode()).hexdigest()[:24] + '.json'


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values(): yield from json_objects(item)
    elif isinstance(value, list):
        for item in value: yield from json_objects(item)
    elif isinstance(value, str) and value.startswith(('{', '[')):
        try: parsed = json.loads(value)
        except ValueError: return
        yield from json_objects(parsed)


def meta_html(value):
    if not value: return ''
    value = json.loads(value)['__html']
    value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), value)
    return value.replace('\\/', '/').replace('\\"', '"').replace('\\n', '\n')


def parse_page(company, source):
    if company == 'apple':
        marker = 'window.__staticRouterHydrationData = JSON.parse('
        encoded, _ = json.JSONDecoder().raw_decode(source.split(marker, 1)[1])
        x = json.loads(encoded)['loaderData']['jobDetails']['jobsData']
        result = sections(*[(title, x.get(key)) for key, title in [
            ('jobSummary', 'Summary'), ('description', 'Description'),
            ('keyQualifications', 'Key Qualifications'), ('minimumQualifications', 'Minimum Qualifications'),
            ('preferredQualifications', 'Preferred Qualifications'), ('educationAndExperience', 'Education & Experience'),
            ('additionalRequirements', 'Additional Requirements'), ('payAndBenefits', 'Pay & Benefits')]])
        for footer in x.get('postingFooters', []):
            for item in footer.get('localizations', {}).get(x.get('selectedLocale', 'en_US'), []):
                entry = sections((item.get('name', ''), item.get('content')))
                if entry and entry[0] not in result: result.extend(entry)
        return result
    if company == 'goldman':
        match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', source, re.S)
        role = json.loads(match[1])['props']['pageProps']['role']
        return sections(('', role['descriptionHtml']))
    if company == 'meta':
        for raw in re.findall(r'<script[^>]*>(.*?)</script>', source, re.S):
            try: payload = json.loads(raw)
            except ValueError: continue
            for x in json_objects(payload):
                if 'minimum_qualifications' not in x or 'responsibilities' not in x: continue
                bullet = lambda key: '\n'.join('• ' + item['item'] for item in x.get(key, []))
                return sections(('Description', meta_html(x.get('description'))),
                    ('Responsibilities', bullet('responsibilities')), ('Minimum Qualifications', bullet('minimum_qualifications')),
                    ('Preferred Qualifications', bullet('preferred_qualifications')),
                    ('About Meta', meta_html(x.get('boiler_plate_intro'))),
                    ('Equal Employment Opportunity', meta_html(x.get('equal_opportunity_message'))),
                    ('Accommodations', meta_html(x.get('accommodations_message'))),
                    ('Additional Information', meta_html(x.get('intl_disclaimer'))),
                    ('California Disclaimer', meta_html(x.get('california_disclaimer'))))
        raise ValueError('Meta JD unavailable')
    tree = Document(source).root
    if company in {'amazon', 'aws'}:
        nodes = tree.find(lambda n: n.tag == 'div' and n.attrs.get('class') == 'section' and any(isinstance(c, Node) and c.tag == 'h2' for c in n.children))
    elif company == 'walmart':
        nodes = tree.find(lambda n: n.tag == 'article' and 'description-section' in n.attrs.get('class', '').split())
    elif company == 'google':
        nodes = tree.find(lambda n: n.tag == 'div' and any(isinstance(c, Node) and c.tag == 'h3' for c in n.children))
        for node in nodes:
            first_heading = next(i for i, c in enumerate(node.children) if isinstance(c, Node) and c.tag == 'h3')
            node.children = node.children[first_heading:]
    else:
        raise ValueError('Unsupported detail source')
    # Already parsed text must not be reparsed as HTML (it may contain literal < signs).
    return [{'heading': '', 'text': normalize_text(n.text())} for n in nodes if n.text().strip()]


class DetailStore:
    def __init__(self, request, root=ROOT):
        self.request, self.root = request, Path(root)
        self.locks = [threading.Lock() for _ in range(32)]
        self.microsoft_limit = threading.Lock()
        self.airwallex_lock = threading.Lock()
        self.airwallex_jobs, self.airwallex_day = {}, None

    def save(self, job, content):
        if not content or sum(len(s['text']) for s in content) < 80:
            raise ValueError('Official JD body unavailable')
        data = {'id': job['id'], 'title': job['title'], 'sourceUrl': job['url'], 'sections': content,
                'fetchedAt': datetime.now(TZ).isoformat(timespec='seconds'), 'stale': False,
                'parserVersion': 2 if job['company'] == 'meta' else 1}
        data.update(translation_info(data))
        path = self.root / 'public' / detail_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as temp:
            json.dump(data, temp, ensure_ascii=False)
        Path(temp.name).replace(path)
        return data

    def fetch(self, job):
        company, id = job['company'], job['id'].split(':', 1)[1]
        if company in {'tencent', 'alibaba'}:
            from chinese_sources import fetch_detail
            return fetch_detail(self.request, job)
        if company == 'microsoft':
            with self.microsoft_limit:
                x = json.loads(self.request('https://apply.careers.microsoft.com/api/pcsx/position_details?position_id=' + id + '&domain=microsoft.com'))['data']
            return sections(('', x['jobDescription']))
        if company in WORKDAY:
            tenant, site, wd = WORKDAY[company]
            path = '/job/' + urlsplit(job['url']).path.split('/job/', 1)[1]
            x = json.loads(self.request(f'https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}' + path))['jobPostingInfo']
            return sections(('', x['jobDescription']))
        if company in {'hsbc', 'hkjc', 'standardchartered', 'oracle', 'sap', 'servicenow', 'janestreet'}:
            import hk_sources
            if company == 'hsbc': return hk_sources.hsbc_sections(self.request, id)
            if company == 'oracle': return hk_sources.oracle_sections(self.request, id)
            if company == 'hkjc': return hk_sources.hkjc_sections(self.request, job['url'])
            if company == 'standardchartered':
                return hk_sources.standardchartered_sections(self.request, job['url'])
            if company == 'janestreet': return hk_sources.jane_sections(self.request, id)
            return hk_sources.feed_sections(self.request, company, id)
        if company == 'airwallex':
            today = datetime.now(TZ).date().isoformat()
            with self.airwallex_lock:
                if self.airwallex_day != today:
                    data = json.loads(self.request('https://api.ashbyhq.com/posting-api/job-board/airwallex'))
                    self.airwallex_jobs = {j['id']: j for j in data['jobs']}
                    self.airwallex_day = today
            x = self.airwallex_jobs[id]
            return sections(('', x.get('descriptionHtml') or x['descriptionPlain']))
        if company == 'goldman':
            document = self.request(job['url'])
            content = parse_page(company, document)
            if content: return content
            match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', document, re.S)
            role = json.loads(match[1])['props']['pageProps']['role']
            external = role.get('externalSource') or {}
            url = urlsplit(external.get('externalApplicationUrl', ''))
            site = re.search(r'/sites/([^/]+)/job/', url.path)
            if url.hostname != 'hdpc.fa.us2.oraclecloud.com' or not site or str(external.get('sourceId')) != id:
                raise ValueError('Goldman official external JD unavailable')
            qs = urlencode({'onlyData': 'true', 'expand': 'all', 'finder': f'ById;Id="{id}",siteNumber="{site[1]}"'})
            rows = json.loads(self.request('https://hdpc.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?' + qs))['items']
            x = next(x for x in rows if str(x['Id']) == id)
            return sections(('Description', x.get('ExternalDescriptionStr')), ('About the program / company', x.get('CorporateDescriptionStr')),
                ('About the division', x.get('OrganizationDescriptionStr')), ('Responsibilities', x.get('ExternalResponsibilitiesStr')),
                ('Qualifications', x.get('ExternalQualificationsStr')))
        return parse_page(company, self.request(job['url']))

    def get(self, job):
        with self.locks[int(hashlib.sha256(job['id'].encode()).hexdigest()[:2], 16) % len(self.locks)]:
            cached = None
            path = self.root / 'public' / detail_path(job)
            if path.exists():
                try: cached = json.loads(path.read_text(encoding='utf-8'))
                except (ValueError, OSError): pass
            version = 2 if job['company'] == 'meta' else 1
            if cached and cached.get('parserVersion', 1) == version and cached.get('fetchedAt', '')[:10] == datetime.now(TZ).date().isoformat():
                return {**cached, **translation_info(cached)}
            try: return self.save(job, self.fetch(job))
            except Exception:
                if cached: return {**cached, **translation_info(cached), 'stale': True}
                raise

    def populate(self, jobs):
        failures = []
        def one(job):
            job['detailPath'] = detail_path(job)
            if job.get('status') == 'missing': return
            try:
                if job.get('jdSections'):
                    result = self.save(job, job.pop('jdSections'))
                else: result = self.get(job)
                job['detailStatus'] = 'stale' if result.get('stale') else 'ok'
                if result.get('stale'): failures.append(job['id'])
            except Exception:
                job['detailStatus'] = 'error'
                failures.append(job['id'])
            finally: job.pop('jdSections', None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, jobs))
        return failures
