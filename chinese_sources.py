"""Public Tencent and Alibaba recruitment sources, using anonymous official sessions."""
import concurrent.futures
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
TENCENT_API = 'https://careers.tencent.com/tencentcareer/api/'
# Official business sites linked by talent.alibaba.com. Separate legacy systems
# (Fliggy and Freshippo) are not included in this adapter.
ALIBABA_SITES = {
    'talent-holding.alibaba.com': '阿里巴巴控股集团',
    'talent.taotian.com': '淘天集团',
    'talent.ele.me': '淘宝闪购',
    'aidc-jobs.alibaba.com': '阿里国际数字商业集团',
    'careers.aliyun.com': '阿里云',
    'careers-tongyi.alibaba.com': 'Token Foundry',
    'talent.dingtalk.com': '千问办公',
    'talent.quark.cn': '千问事业部',
    'recruitment.t-head.cn': '平头哥',
    'talent.amap.com': '高德地图',
    'talent.cainiao.com': '菜鸟',
    'jobs.hujing-dme.com': '虎鲸文娱集团',
    'careers.alihealth.cn': '阿里健康',
    'talent.lingxigames.com': '灵犀互娱',
    'talent-post.alibaba.com': '菜鸟驿站',
    'talent.agtech.com': '亚博科技',
    'jobs.alisports.com': '橙狮体育',
}
CAMPUS_HOST = 'campus-talent.alibaba.com'


class AlibabaSession:
    def __init__(self, host):
        if host not in ALIBABA_SITES and host != CAMPUS_HOST:
            raise ValueError('Unknown Alibaba recruitment host')
        self.base = 'https://' + host
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.headers = {'User-Agent': 'Mozilla/5.0 (compatible; SouthboundJobs/1.0)',
                        'Accept': 'application/json,text/html', 'Referer': self.base + '/'}
        with self.opener.open(urllib.request.Request(self.base + '/', headers=self.headers), timeout=35) as r:
            r.read()
        self.token = next((c.value for c in self.jar if c.name == 'XSRF-TOKEN'), None)
        if not self.token:
            raise ValueError('Alibaba public session unavailable: ' + host)

    def post(self, path, body=None):
        url = self.base + path + '?' + urllib.parse.urlencode({'_csrf': self.token})
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=json.dumps(body or {}).encode(),
                    headers={**self.headers, 'Content-Type': 'application/json'})
                with self.opener.open(req, timeout=35) as response:
                    data = json.load(response)
                if data.get('success') is not True:
                    raise ValueError('Alibaba public API did not return success')
                return data['content']
            except Exception:
                if attempt == 2: raise
                time.sleep(2 + attempt * 3)


def tencent_payload(request, path, params):
    data = json.loads(request(TENCENT_API + path + '?' + urllib.parse.urlencode(params)))
    if data.get('Code') != 200 or not isinstance(data.get('Data'), dict):
        raise ValueError('Tencent public API unavailable')
    return data['Data']


def tencent_sections(x):
    from job_details import sections
    return sections(('岗位职责', x.get('Responsibility')), ('岗位要求', x.get('Requirement')),
        ('岗位亮点', x.get('PostLightItem')), ('加分项', x.get('ImportantItem')),
        ('岗位介绍', x.get('Introduction')), ('部门介绍', x.get('DepartmentIntroduction')),
        ('工作经验', x.get('RequireWorkYearsName')))


def alibaba_sections(x):
    from job_details import sections
    extra = []
    if x.get('degree'):
        degree = {'bachelor': '本科', 'master': '硕士', 'doctor': '博士', 'college': '大专', 'no_limit': '不限'}.get(x['degree'], x['degree'])
        extra.append(('学历要求', degree))
    exp = x.get('experience') or {}
    if exp.get('from') is not None:
        extra.append(('工作经验', f"{exp['from']}–{exp['to']} 年" if exp.get('to') is not None else f"{exp['from']} 年及以上"))
    if x.get('graduationTime'): extra.append(('毕业时间要求', x['graduationTime']))
    if x.get('batchName'): extra.append(('招聘项目', x['batchName']))
    return sections(('职位描述', x.get('description')), ('职位要求', x.get('requirement')), *extra)


def fetch_detail(request, job):
    from job_details import sections
    id = job['id'].split(':', 1)[1]
    if job['company'] == 'tencent':
        x = tencent_payload(request, 'post/ByPostId', {'postId': id, 'language': 'zh-cn'})
        if str(x.get('PostId')) != id: raise ValueError('Tencent JD ID mismatch')
        return tencent_sections(x)
    host = urllib.parse.urlsplit(job['url']).hostname
    session = AlibabaSession(host)
    body = {'id': id, 'language': 'zh', 'channel': 'campus_group_official_site' if host == CAMPUS_HOST else 'group_official_site'}
    if job.get('batchId'): body['batchId'] = job['batchId']
    x = session.post('/position/detail', body)
    if str(x.get('id')) != id: raise ValueError('Alibaba JD ID mismatch')
    return alibaba_sections(x)


class ChineseSources:
    def __init__(self, request, make_job, cities_for, plain):
        self.request, self.make_job, self.cities_for, self.plain = request, make_job, cities_for, plain

    def tencent(self):
        from source_adapters import check_page
        dictionary = tencent_payload(self.request, 'data/GetMultiDictionary', {'language': 'zh-cn', 'type': 'WorkPlace,PostAttr'})
        cities = [str(x['Code']) for x in dictionary['WorkPlace'] if self.cities_for([x['Name']])]
        if len(cities) < 4: raise ValueError('Tencent target city dictionary changed')
        out = {}
        for attr in dictionary['PostAttr']:
            seen, processed = set(), 0
            for page in range(1, 151):
                data = tencent_payload(self.request, 'post/Query', {'cityId': ','.join(cities), 'attrId': attr['Code'],
                    'pageIndex': page, 'pageSize': 100, 'language': 'zh-cn', 'area': 'cn'})
                rows, total = data['Posts'], data['Count']
                check_page(rows, total, processed, seen, {str(x['PostId']) for x in rows}, 'Tencent')
                for x in rows:
                    if x.get('IsValid') is False: continue
                    job = self.make_job('tencent', x['PostId'], x['RecruitPostName'], [x['LocationName']],
                        'https://careers.tencent.com/jobdesc.html?postId=' + str(x['PostId']),
                        ' / '.join(filter(None, [x.get('BGName'), x.get('ProductName'), x.get('CategoryName')])),
                        summary=x.get('Responsibility', '')[:1000],
                        kind='实习 / 校招' if '校' in attr['Name'] or '实习' in attr['Name'] else '社招 / 其他')
                    if job:
                        # The source publishes an update date, not a posting date.
                        job['sourceUpdatedAt'] = x.get('LastUpdateTime')
                        out[job['id']] = job
                processed += len(rows)
                if processed >= total: break
                time.sleep(.2)
            else: raise ValueError('Tencent pagination limit')
        return list(out.values())

    def alibaba(self):
        from source_adapters import check_page
        def site(host):
            session = AlibabaSession(host)
            campus = host == CAMPUS_HOST
            batches = [None]
            if campus:
                data = session.post('/searchCondition/listBatch')
                batches = list(dict.fromkeys(x['id'] for value in data.values() if isinstance(value, list)
                    for x in value if isinstance(x, dict) and 'id' in x))
                if not batches: raise ValueError('Alibaba campus batches unavailable')
            out = {}
            for batch in batches:
                processed, seen = 0, set()
                for page in range(1, 301):
                    body = {'channel': 'campus_group_official_site' if campus else 'group_official_site',
                        'language': 'zh', 'pageIndex': page, 'pageSize': 20}
                    # Social search caps unfiltered results at 500 on some sites.
                    # Official region codes keep each query complete and relevant.
                    # Campus uses a different filter format: even an empty social
                    # regions/key filter can silently produce an empty response.
                    if not campus: body.update(regions='440300,440100,HKG,SINGAPORE', key='')
                    if batch is not None: body['batchId'] = batch
                    data = session.post('/position/search', body)
                    rows, total = data['datas'] or [], data['totalCount']
                    check_page(rows, total, processed, seen, {str(x['id']) for x in rows}, 'Alibaba ' + host)
                    for x in rows:
                        locs = x.get('workLocations') or []
                        url = urllib.parse.urljoin(session.base, x['positionUrl'])
                        parts = urllib.parse.urlsplit(url)
                        if parts.hostname != host: raise ValueError('Alibaba unexpected JD host')
                        # Remove only tracking parameters; keep batch and official detail route.
                        query = [(k,v) for k,v in urllib.parse.parse_qsl(parts.query) if k not in {'track_id', 'trackId'}]
                        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))
                        posted = datetime.fromtimestamp(x['publishTime'] / 1000, TZ).isoformat() if x.get('publishTime') else None
                        department = ' / '.join(dict.fromkeys(filter(None, [ALIBABA_SITES.get(host, '阿里巴巴校园招聘'), x.get('department')])))
                        job = self.make_job('alibaba', x['id'], x['name'], locs, url, department, posted,
                            self.plain(x.get('description', ''))[:1000], kind='实习 / 校招' if campus else '社招 / 其他')
                        if job:
                            job['jdSections'] = alibaba_sections(x)
                            if batch is not None: job['batchId'] = batch
                            out[job['id']] = job
                    processed += len(rows)
                    if processed >= total: break
                    time.sleep(.2)
                else: raise ValueError('Alibaba pagination limit: ' + host)
            print(f'Alibaba {host}: {len(out)} matching jobs', flush=True)
            return list(out.values())
        out = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for jobs in pool.map(site, [*ALIBABA_SITES, CAMPUS_HOST]):
                for job in jobs:
                    prior = out.get(job['id'])
                    if prior:
                        job['locations'] = list(dict.fromkeys(prior['locations'] + job['locations']))
                        job['cities'] = self.cities_for(job['locations'])
                    out[job['id']] = job
        return list(out.values())
