"""Public employer sources. Python 3.10+, standard library only."""
from __future__ import annotations
import concurrent.futures
import html
import json
import re
import threading
import time
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from job_details import DetailStore, sections

ROOT = Path(os.environ.get('SOUTHBOUND_DATA_ROOT', Path(__file__).resolve().parent))
DATA = ROOT / 'data' / 'jobs.json'
TZ = timezone(timedelta(hours=8))
CITIES = {'深圳': 'Shenzhen', '香港': 'Hong Kong', '新加坡': 'Singapore', '广州': 'Guangzhou'}
LOCK = threading.Lock()

def process_lock():
    """OS lock is released even if the process exits unexpectedly."""
    file = open(ROOT / '.sync.lock', 'a+b')
    try:
        # Windows locks are mandatory: do not read an already-locked byte.
        if os.fstat(file.fileno()).st_size == 0:
            file.write(b'0')
            file.flush()
        file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return file
    except (OSError, IOError):
        file.close()
        return None

def is_running():
    if LOCK.locked(): return True
    lock = process_lock()
    if lock is None: return True
    lock.close()
    return False
COMPANIES = [
    {'id': 'microsoft', 'name': '微软', 'en': 'Microsoft', 'mark': 'MS', 'color': '#426954', 'url': 'https://apply.careers.microsoft.com/careers', 'source': 'microsoft', 'scope': '香港周边（官网默认 160 公里）及新加坡检索，再按四城实际工作地点筛选'},
    {'id': 'amazon', 'name': '亚马逊', 'en': 'Amazon', 'mark': 'a', 'color': '#9b5c23', 'url': 'https://www.amazon.jobs/en/search', 'source': 'amazon', 'scope': '四城公开职位；AWS 单独展示'},
    {'id': 'aws', 'name': '亚马逊云科技', 'en': 'Amazon Web Services', 'mark': 'aws', 'color': '#9b5c23', 'url': 'https://www.amazon.jobs/content/en/teams/amazon-web-services', 'source': 'amazon', 'scope': '按官方业务标签、雇佣实体或标题识别 AWS'},
    {'id': 'goldman', 'name': '高盛', 'en': 'Goldman Sachs', 'mark': 'GS', 'color': '#437487', 'url': 'https://higher.gs.com/', 'source': 'goldman', 'scope': '四城关键词检索，含社招、初级岗位和校园项目'},
    {'id': 'walmart', 'name': '沃尔玛', 'en': 'Walmart', 'mark': '✳', 'color': '#2b6ba0', 'url': 'https://walmartchina.avature.cn/zh_CN/careers', 'source': 'walmart', 'scope': '沃尔玛中国支持中心招聘，覆盖深圳、广州；不含门店及港新岗位'},
    {'id': 'tencent', 'name': '腾讯', 'en': 'Tencent', 'mark': 'T', 'color': '#2b6ba0', 'url': 'https://careers.tencent.com/', 'source': 'tencent', 'scope': '腾讯官方四城职位，含社招、校招、实习和校招专项；官网更新日期不当作发布日期'},
    {'id': 'bytedance', 'name': '字节跳动', 'en': 'ByteDance', 'mark': 'B', 'color': '#397c8a', 'url': 'https://jobs.bytedance.com/', 'source': None, 'scope': '官方招聘入口，暂未接入职位同步'},
    {'id': 'alibaba', 'name': '阿里巴巴', 'en': 'Alibaba', 'mark': '阿', 'color': '#ac6429', 'url': 'https://talent.alibaba.com/', 'source': 'alibaba', 'scope': '17 个官方业务站及统一校招/实习站，按四城实际地点去重；含阿里云、淘天、国际业务、千问等，不含飞猪及盒马独立招聘系统'},
    {'id': 'google', 'name': '谷歌', 'en': 'Google', 'mark': 'G', 'color': '#4966aa', 'url': 'https://www.google.com/about/careers/applications/jobs/results/', 'source': 'google', 'scope': 'Google Careers 四城职位，含官网列出的 YouTube 等团队'},
    {'id': 'nvidia', 'name': '英伟达', 'en': 'NVIDIA', 'mark': 'NV', 'color': '#527a2a', 'url': 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite', 'source': 'nvidia', 'scope': '官方 Workday 职位，按主工作地点及其他可选地点筛选四城'},
    {'id': 'apple', 'name': '苹果', 'en': 'Apple', 'mark': 'A', 'color': '#555b62', 'url': 'https://jobs.apple.com/en-us/search', 'source': 'apple', 'scope': '四城官方职位，含技术和零售；未明确城市的全国通用岗位不计入'},
    {'id': 'cisco', 'name': '思科', 'en': 'Cisco', 'mark': 'CS', 'color': '#33718c', 'url': 'https://careers.cisco.com/global/en', 'source': 'cisco', 'scope': '思科官方 Workday 四城职位，含该官网发布的 Splunk 岗位'},
    {'id': 'meta', 'name': 'Meta', 'en': 'Meta', 'mark': 'M', 'color': '#3768a3', 'url': 'https://www.metacareers.com/jobsearch/', 'source': 'meta', 'scope': 'Meta Careers 四城检索，按实际工作地点筛选'},
    {'id': 'airwallex', 'name': '空中云汇', 'en': 'Airwallex', 'mark': 'AW', 'color': '#7550a1', 'url': 'https://www.airwallex.com/careers', 'source': 'airwallex', 'scope': '官方 Ashby 招聘职位，包含主工作地点和其他可选地点'},
    {'id': 'shopee', 'name': '虾皮', 'en': 'Shopee', 'mark': 'S', 'color': '#b75835', 'url': 'https://careers.shopee.sg/', 'source': None, 'scope': '官方招聘入口，暂未接入职位同步'},
    {'id': 'hsbc', 'name': '汇丰', 'en': 'HSBC', 'mark': 'H', 'color': '#c63f3f', 'url': 'https://portal.careers.hsbc.com/', 'source': 'hsbc', 'scope': '汇丰官方招聘门户，按香港、新加坡、广州三个检索半径取并集后去重，再按四城实际地点筛选'},
    {'id': 'standardchartered', 'name': '渣打银行', 'en': 'Standard Chartered', 'mark': 'SC', 'color': '#2b7a78', 'url': 'https://jobs.standardchartered.com/', 'source': 'standardchartered', 'scope': '渣打官方招聘站，按香港、新加坡、中国内地三个国家/地区检索，再按四城实际地点筛选'},
    {'id': 'aia', 'name': '友邦保险', 'en': 'AIA', 'mark': 'AIA', 'color': '#b23a48', 'url': 'https://aia.wd3.myworkdayjobs.com/External', 'source': 'aia', 'scope': '友邦官方 Workday 职位，按主要工作地点筛选四城'},
    {'id': 'hkex', 'name': '香港交易所', 'en': 'HKEX', 'mark': 'HK', 'color': '#3f6f9c', 'url': 'https://hkex.wd3.myworkdayjobs.com/HKEXCareerPage', 'source': 'hkex', 'scope': '港交所官方 Workday 职位；官网仅按国家/地区筛选，城市以职位详情回填'},
    {'id': 'hkjc', 'name': '香港赛马会', 'en': 'The Hong Kong Jockey Club', 'mark': 'JC', 'color': '#8a2f3b', 'url': 'https://careers.hkjc.com/', 'source': 'hkjc', 'scope': '赛马会官方招聘站全量职位，工作地点集中在香港'},
    {'id': 'huawei', 'name': '华为', 'en': 'Huawei', 'mark': 'HW', 'color': '#c4302b', 'url': 'https://career.huawei.com/', 'source': None, 'scope': '官方招聘入口，暂未接入职位同步'},
    {'id': 'oracle', 'name': '甲骨文', 'en': 'Oracle', 'mark': 'O', 'color': '#b4423a', 'url': 'https://careers.oracle.com/jobs/', 'source': 'oracle', 'scope': 'Oracle 官方招聘 API，按四城关键词检索后依实际地点筛选'},
    {'id': 'sap', 'name': 'SAP', 'en': 'SAP', 'mark': 'SAP', 'color': '#2b6ba0', 'url': 'https://jobs.sap.com/', 'source': 'sap', 'scope': 'SAP 官方职位数据源（全量 XML），按四城实际地点筛选，JD 随数据源一并提供'},
    {'id': 'salesforce', 'name': 'Salesforce', 'en': 'Salesforce', 'mark': 'SF', 'color': '#2b8fc4', 'url': 'https://careers.salesforce.com/', 'source': 'salesforce', 'scope': 'Salesforce 官方 Workday 职位，按工作地点筛选四城'},
    {'id': 'servicenow', 'name': 'ServiceNow', 'en': 'ServiceNow', 'mark': 'SN', 'color': '#2f7d64', 'url': 'https://careers.servicenow.com/', 'source': 'servicenow', 'scope': 'ServiceNow 官方职位数据源（全量 XML），按四城实际地点筛选，JD 随数据源一并提供'},
    {'id': 'janestreet', 'name': '简街', 'en': 'Jane Street', 'mark': 'JS', 'color': '#2b5f8c', 'url': 'https://www.janestreet.com/join-jane-street/', 'source': 'janestreet', 'scope': 'Jane Street 官方全量职位数据源，按香港、新加坡等实际工作地点筛选四城，JD 随数据源一并提供'},
]

def now():
    return datetime.now(TZ).isoformat(timespec='seconds')

def request(url, body=None, *, form=None, extra_headers=None):
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; SouthboundJobs/1.0)', 'Accept': 'application/json,text/html'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    payload = json.dumps(body).encode() if body is not None else None
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        payload = urllib.parse.urlencode(form).encode()
    headers.update(extra_headers or {})
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=35) as response:
                return response.read().decode('utf-8')
        except Exception as error:
            if attempt == 2:
                raise
            retry = 2 + attempt * 3
            if isinstance(error, urllib.error.HTTPError) and error.code == 429:
                try: retry = max(15, min(90, int(error.headers.get('Retry-After', '20'))))
                except ValueError: retry = 20
            time.sleep(retry)

def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(text or '')))).strip()

DETAILS = DetailStore(request)

def cities_for(locations):
    text = ' | '.join(str(x) for x in locations).lower()
    return [cn for cn, en in CITIES.items() if cn in text or en.lower() in text]

def category(title, department=''):
    t = (title + ' ' + department).lower()
    if re.search(r'\bai\b|machine learning|data scien|applied scien|人工智能|算法|大模型', t): return 'AI / 数据'
    if re.search(r'cloud|solution.*(architect|engineer)|云|架构|devops|\bsre\b|security|安全', t): return '云计算 / 安全'
    if re.search(r'software|developer|engineer|technical|engineering|研发|开发|技术|科技|测试', t): return '研发 / 工程'
    if re.search(r'product|program manag|design|产品|设计|项目', t): return '产品 / 设计'
    if re.search(r'sales|account|marketing|business develop|销售|客户|市场', t): return '销售 / 市场'
    if re.search(r'financ|compliance|treasury|risk|banking|audit|quant|税|财务|合规|投行|风控', t): return '金融 / 风控'
    return '运营 / 其他'

def role_type(title):
    return '实习 / 校招' if re.search(r'\bintern\b|internship|graduate|campus|summer|实习|校招|应届', title, re.I) else '社招 / 其他'

def make_job(company, id, title, locations, url, department='', posted=None, summary='', kind=None):
    cities = cities_for(locations)
    if not cities:
        return None
    return {'id': f'{company}:{id}', 'company': company, 'title': plain(title), 'cities': cities,
            'locations': locations, 'url': url, 'department': plain(department), 'postedAt': posted,
            'summary': plain(summary), 'category': category(title, department), 'type': kind or role_type(title)}

def collect_microsoft():
    out = {}
    # The official 160 km Hong Kong catchment includes Shenzhen and Guangzhou.
    # Search the two distinct regions once to avoid duplicate, rate-limited requests.
    for city in ['Hong Kong', 'Singapore']:
        start, seen = 0, set()
        while True:
            qs = urllib.parse.urlencode({'domain': 'microsoft.com', 'location': city, 'start': start, 'num': 100})
            data = json.loads(request('https://apply.careers.microsoft.com/api/pcsx/search?' + qs))['data']
            rows = data['positions']
            if not rows:
                if start < data['count']: raise ValueError('Microsoft pagination ended before count')
                break
            ids = {str(x['id']) for x in rows}
            if ids <= seen: raise ValueError('Microsoft pagination repeated')
            seen |= ids
            for x in rows:
                posted = datetime.fromtimestamp(x['postedTs'], TZ).isoformat() if x.get('postedTs') else None
                j = make_job('microsoft', x['id'], x['name'], x['locations'], urllib.parse.urljoin('https://apply.careers.microsoft.com', x['positionUrl']), x.get('department', ''), posted)
                if j: out[j['id']] = j
            start += len(rows)
            if start >= data['count']: break
            if start > 2000: raise ValueError('Microsoft pagination limit')
            time.sleep(2)
    return list(out.values())

def collect_amazon():
    out = {}
    for city in CITIES.values():
        offset, seen = 0, set()
        while True:
            location_filter = {'normalized_country_code[]': 'SGP'} if city == 'Singapore' else {'normalized_city_name[]': city}
            qs = urllib.parse.urlencode({'offset': offset, 'result_limit': 100, 'sort': 'recent', **location_filter})
            data = json.loads(request('https://www.amazon.jobs/en/search.json?' + qs))
            if data.get('error'): raise ValueError(str(data['error']))
            rows = data['jobs']
            if not rows:
                if offset < data['hits']: raise ValueError('Amazon pagination ended before count')
                break
            ids = {x['id'] for x in rows}
            if ids <= seen: raise ValueError('Amazon pagination repeated')
            seen |= ids
            for x in rows:
                locs = [x.get('location', '')]
                for loc in x.get('locations', []):
                    loc = json.loads(loc) if isinstance(loc, str) else loc
                    locs.append(loc.get('normalizedLocation') or loc.get('location') or loc.get('city', ''))
                aws_text = ' '.join(str(x.get(k) or '') for k in ['business_category', 'company_name', 'title', 'primary_search_label'])
                co = 'aws' if re.search(r'\baws\b|amazon web services|amazon-web-services', aws_text, re.I) else 'amazon'
                try: posted = datetime.strptime(x['posted_date'], '%B %d, %Y').replace(tzinfo=TZ).isoformat()
                except (KeyError, ValueError): posted = None
                j = make_job(co, x['id_icims'], x['title'], list(dict.fromkeys(locs)), 'https://www.amazon.jobs' + x['job_path'], x.get('job_category', ''), posted, x.get('description_short', ''))
                if j:
                    j['jdSections'] = sections(('Description', x.get('description')), ('Basic Qualifications', x.get('basic_qualifications')), ('Preferred Qualifications', x.get('preferred_qualifications')))
                    out[j['id']] = j
            offset += len(rows)
            if offset >= data['hits']: break
            if offset > 3000: raise ValueError('Amazon pagination limit')
            time.sleep(.15)
    return list(out.values())

GS_QUERY = '''query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
 roleSearch(searchQueryInput: $searchQueryInput) { totalCount items {
 roleId corporateTitle jobTitle jobFunction locations {primary state country city}
 status division skills jobType {code description} externalSource {sourceId}
 } } }'''

def collect_goldman():
    out = {}
    for city in CITIES.values():
        page, seen = 0, set()
        while True:
            body = {'operationName': 'GetRoles', 'query': GS_QUERY, 'variables': {'searchQueryInput': {
                'page': {'pageSize': 100, 'pageNumber': page}, 'sort': {'sortStrategy': 'RELEVANCE', 'sortOrder': 'DESC'},
                'filters': [], 'experiences': ['EARLY_CAREER', 'PROFESSIONAL', 'CAMPUS'], 'searchTerm': city}}}
            data = json.loads(request('https://api-higher.gs.com/gateway/api/v1/graphql', body))
            if data.get('errors'): raise ValueError(str(data['errors'])[:200])
            data = data['data']['roleSearch']
            rows = data['items']
            if not rows:
                if page * 100 < data['totalCount']: raise ValueError('Goldman pagination ended before count')
                break
            ids = {x['roleId'] for x in rows}
            if ids <= seen: raise ValueError('Goldman pagination repeated')
            seen |= ids
            for x in rows:
                if x['status'] != 'POSTED': continue
                locs = [', '.join(str(l.get(k) or '') for k in ['city', 'state', 'country']) for l in x['locations']]
                id = (x.get('externalSource') or {}).get('sourceId')
                if not id: continue
                j = make_job('goldman', id, x['jobTitle'], locs, f'https://higher.gs.com/roles/{id}', x.get('jobFunction') or x.get('division') or '', summary=' · '.join(filter(None, [x.get('corporateTitle'), x.get('division')])))
                if j: out[j['id']] = j
            page += 1
            if page * 100 >= data['totalCount']: break
            if page > 30: raise ValueError('Goldman pagination limit')
            time.sleep(.15)
    return list(out.values())

def parse_walmart(s):
    if 'article--result' not in s: raise ValueError('Walmart markup changed or empty result requires review')
    out = []
    articles = re.findall(r'<article\b[^>]*class="[^"]*article--result[^"]*"[^>]*>(.*?)</article>', s, re.S)
    if not articles: raise ValueError('Walmart result parser found no articles')
    for a in articles:
        title = re.search(r'<h3\b[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', a, re.S)
        loc = re.search(r'class="article--result_location"[^>]*>(.*?)</span>', a, re.S)
        date = re.search(r'class="article--result_created"[^>]*>(.*?)</span>', a, re.S)
        if not title or not loc: raise ValueError('Walmart article missing required fields')
        posted = datetime.strptime(plain(date[1]), '%m/%d/%y').replace(tzinfo=TZ).isoformat() if date else None
        url = html.unescape(title[1])
        j = make_job('walmart', url.rstrip('/').split('/')[-1], plain(title[2]), [plain(loc[1])], url, '支持中心（总部）', posted)
        if j: out.append(j)
    offsets = [int(x) for x in re.findall(r'jobOffset=(\d+)', s)]
    return out, max(offsets, default=0)

def collect_walmart():
    out, offset, seen = {}, 0, set()
    while True:
        s = request(f'https://walmartchina.avature.cn/zh_CN/careers/SearchJobs/?jobRecordsPerPage=10&jobOffset={offset}')
        signature = re.findall(r'href="([^"?]+/JobDetail/[^"?]+)"', s)
        sig = tuple(signature)
        if sig in seen: raise ValueError('Walmart pagination repeated')
        seen.add(sig)
        rows, last = parse_walmart(s)
        for j in rows: out[j['id']] = j
        if offset >= last: break
        offset += 10
        if offset > 2000: raise ValueError('Walmart pagination limit')
        time.sleep(.15)
    return list(out.values())

from source_adapters import PublicSources
PUBLIC = PublicSources(request, make_job, cities_for, plain)
from chinese_sources import ChineseSources
CHINESE = ChineseSources(request, make_job, cities_for, plain)
from hk_sources import ExtraSources
EXTRA = ExtraSources(request, make_job, cities_for, plain)
COLLECTORS = {'microsoft': collect_microsoft, 'amazon': collect_amazon, 'goldman': collect_goldman, 'walmart': collect_walmart,
              'nvidia': PUBLIC.nvidia, 'apple': PUBLIC.apple, 'cisco': PUBLIC.cisco,
              'meta': PUBLIC.meta, 'airwallex': PUBLIC.airwallex, 'google': PUBLIC.google,
              'tencent': CHINESE.tencent, 'alibaba': CHINESE.alibaba,
              'hsbc': EXTRA.hsbc, 'standardchartered': EXTRA.standardchartered, 'hkjc': EXTRA.hkjc,
              'oracle': EXTRA.oracle, 'sap': EXTRA.sap, 'servicenow': EXTRA.servicenow,
              'janestreet': EXTRA.janestreet,
              'aia': lambda: PUBLIC.workday('aia', 'aia', 'External', wd='wd3', facet='primaryLocation'),
              'hkex': lambda: PUBLIC.workday('hkex', 'hkex', 'HKEXCareerPage', wd='wd3',
                  facet='locationCountry', countries={'Hong Kong', 'China', 'Singapore'}),
              'salesforce': lambda: PUBLIC.workday('salesforce', 'salesforce', 'External_Career_Site', wd='wd12')}

def read_data():
    if DATA.exists(): return json.loads(DATA.read_text(encoding='utf-8'))
    return {'jobs': [], 'companies': COMPANIES, 'sources': [], 'history': [], 'updatedAt': None, 'baselineAt': None}

def merge(old, results, stamp):
    old_jobs = {j['id']: j for j in old.get('jobs', [])}
    jobs = dict(old_jobs)
    source_map = {c['id']: c['source'] for c in COMPANIES}
    previous_sources = {s['id']: s for s in old.get('sources', [])}
    sources, added, missing = [], 0, 0
    baseline = not old.get('baselineAt')
    for key, result in results.items():
        previous = previous_sources.get(key, {})
        if isinstance(result, Exception):
            sources.append({'id': key, 'status': 'error', 'checkedAt': stamp, 'lastSuccessAt': previous.get('lastSuccessAt'), 'count': previous.get('count', 0), 'message': str(result)[:220]})
            for j in jobs.values():
                if source_map[j['company']] == key and j.get('status') != 'missing': j['status'] = 'stale'
            continue
        result = list({j['id']: j for j in result}.values())
        ids = {j['id'] for j in result}
        for id, j in list(jobs.items()):
            if source_map[j['company']] == key and id not in ids:
                if j.get('status') != 'missing': missing += 1
                jobs[id] = {**j, 'status': 'missing', 'missingSince': j.get('missingSince') or stamp}
        for j in result:
            prior = old_jobs.get(j['id'])
            if not prior: added += 1
            jobs[j['id']] = {**j, 'status': 'active', 'firstSeenAt': prior['firstSeenAt'] if prior else stamp,
                            'lastSeenAt': stamp, 'baseline': prior.get('baseline', False) if prior else (baseline or not previous.get('lastSuccessAt'))}
        sources.append({'id': key, 'status': 'ok', 'checkedAt': stamp, 'lastSuccessAt': stamp, 'count': len(result), 'message': ''})
    ok = sum(s['status'] == 'ok' for s in sources)
    record = {'at': stamp, 'ok': ok, 'total': len(results), 'added': added, 'missing': missing, 'baseline': baseline, 'active': sum(j.get('status') != 'missing' for j in jobs.values())}
    sources.extend(s for key, s in previous_sources.items() if key not in results)
    return {'jobs': list(jobs.values()), 'companies': COMPANIES, 'sources': sources, 'updatedAt': stamp,
            'baselineAt': (old.get('baselineAt') or stamp) if ok else old.get('baselineAt'),
            'history': [record] + old.get('history', [])[:59]}

def refresh(source_ids=None):
    selected = COLLECTORS if source_ids is None else {key: COLLECTORS[key] for key in source_ids}
    if not LOCK.acquire(blocking=False): return None
    file_lock = None
    try:
        file_lock = process_lock()
        if file_lock is None: return None
        old, results = read_data(), {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fn): key for key, fn in selected.items()}
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                    print(f'{key}: {len(results[key])} jobs', flush=True)
                except Exception as e:
                    results[key] = e
                    print(f'{key}: FAILED {e}', flush=True)
        data = merge(old, results, now())
        detail_failures = DETAILS.populate(data['jobs'])
        data['detailFailures'] = detail_failures
        print(f"Full JD: {sum(j.get('detailStatus') == 'ok' for j in data['jobs'])} cached, {len(detail_failures)} unavailable", flush=True)
        DATA.parent.mkdir(parents=True, exist_ok=True)
        temp = DATA.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(DATA)
        # A static-hosting snapshot uses the same data, with no external API credentials.
        snapshot = ROOT / 'public' / 'snapshot.js'
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        temp_snapshot = snapshot.with_suffix('.tmp')
        temp_snapshot.write_text('window.JOBS_SNAPSHOT=' + json.dumps(data, ensure_ascii=False).replace('</', '<\\/') + ';', encoding='utf-8')
        temp_snapshot.replace(snapshot)
        return data
    finally:
        if file_lock is not None: file_lock.close()
        LOCK.release()

if __name__ == '__main__':
    result = refresh()
    if result and (result.get('detailFailures') or not all(s['status'] == 'ok' for s in result['sources'])):
        raise SystemExit(1)
