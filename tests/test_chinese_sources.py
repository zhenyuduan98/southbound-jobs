import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from collector import make_job, cities_for, plain, merge
from chinese_sources import ChineseSources, tencent_sections, alibaba_sections, fetch_detail, AlibabaSession


class ChineseSourceTests(unittest.TestCase):
    def test_tencent_types_location_validation_and_update_date(self):
        def request(url):
            params = parse_qs(urlsplit(url).query)
            if 'GetMultiDictionary' in url:
                return json.dumps({'Code': 200, 'Data': {'WorkPlace': [dict(Code=i, Name=n) for i,n in enumerate(['深圳','广州','中国香港','新加坡'])],
                    'PostAttr': [dict(Code='1',Name='社招'),dict(Code='3',Name='校招实习生')]}})
            page = int(params['pageIndex'][0])
            attr = params['attrId'][0]
            self.assertEqual(params['cityId'], ['0,1,2,3'])
            row = dict(PostId=attr+str(page), RecruitPostName='新加坡软件工程师', LocationName='深圳' if page==1 else '北京', IsValid=True,
                       LastUpdateTime='2026年09月29日', Responsibility='完整职责', BGName='TEG')
            return json.dumps({'Code':200,'Data':{'Count':2,'Posts':[row]}})
        jobs = ChineseSources(request, make_job, cities_for, plain).tencent()
        self.assertEqual(len(jobs),2)
        self.assertEqual({j['type'] for j in jobs},{'社招 / 其他','实习 / 校招'})
        self.assertTrue(all(j['postedAt'] is None for j in jobs))
        self.assertTrue(all(j['cities']==['深圳'] for j in jobs))
        self.assertTrue(all(j['url'].startswith('https://careers.tencent.com/jobdesc.html?postId=') for j in jobs))

    def test_full_jd_includes_requirements_and_additional_sections(self):
        text='1. 第一条\n2. 第二条\n\n第三段' + '完整正文' * 500
        t = tencent_sections({'Responsibility':text,'Requirement':'任职条件','ImportantItem':'优先条件','DepartmentIntroduction':'部门介绍'})
        self.assertEqual(t[0]['text'],text)
        self.assertEqual([s['heading'] for s in t],['岗位职责','岗位要求','加分项','部门介绍'])
        a = alibaba_sections({'description':text,'requirement':'任职条件','degree':'master','experience':{'from':3,'to':5}})
        self.assertEqual(a[0]['text'],text)
        self.assertEqual(a[-1]['text'],'3–5 年')

    def test_detail_rejects_wrong_job_and_unknown_host(self):
        with self.assertRaises(ValueError):
            fetch_detail(lambda _:json.dumps({'Code':200,'Data':{'PostId':'wrong'}}),{'company':'tencent','id':'tencent:123'})
        with self.assertRaises(ValueError): AlibabaSession('example.com')

    @patch('chinese_sources.time.sleep')
    def test_alibaba_pagination_campus_batches_and_cross_site_dedup(self, _):
        requests=[]
        class Session:
            def __init__(self,host): self.host,self.base=host,'https://'+host
            def post(self,path,body=None):
                if path.endswith('listBatch'):
                    return {'graduate':[{'id':1}], 'topTalentPlan':[{'id':1}], 'internship':[{'id':2}], 'sequence':['graduate']}
                requests.append((self.host,body.copy()))
                page=body['pageIndex']
                campus=self.host=='campus-talent.alibaba.com'
                if campus:
                    assert 'regions' not in body and 'key' not in body
                else:
                    assert body['regions']=='440300,440100,HKG,SINGAPORE'
                row={'id': 'shared' if page==1 else 'outside', 'name':'工程师', 'workLocations':['新加坡'] if campus else ['深圳'],
                     'positionUrl':'/campus/position-detail?positionId=shared&track_id=remove' if campus else '/off-campus/position-detail?positionId=shared&track_id=remove',
                     'description':'职责\n完整正文','requirement':'岗位要求','publishTime':1790600000000}
                if page==2:row['workLocations']=['北京']
                return {'datas':[row],'totalCount':2}
        with patch('chinese_sources.ALIBABA_SITES',{'test.alibaba.com':'测试业务'}), patch('chinese_sources.AlibabaSession',Session):
            jobs=ChineseSources(None,make_job,cities_for,plain).alibaba()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['cities'],['深圳','新加坡'])
        self.assertEqual(jobs[0]['type'],'实习 / 校招')
        self.assertNotIn('track_id',jobs[0]['url'])
        self.assertEqual(len(requests),6)
        self.assertEqual({b['batchId'] for host,b in requests if 'batchId' in b},{1,2})
        self.assertTrue(jobs[0]['jdSections'])

    def test_new_sources_baseline_and_failure_preserve_existing(self):
        jobs={co:[make_job(co,'1','工程师',['深圳'],'https://example.com')] for co in ['alibaba','tencent']}
        d=merge({},jobs,'2026-09-29T09:00:00+08:00')
        self.assertTrue(all(j['baseline'] for j in d['jobs']))
        d=merge(d,{'alibaba':RuntimeError('source offline')},'2026-09-30T09:00:00+08:00')
        self.assertEqual({j['company']:j['status'] for j in d['jobs']},{'alibaba':'stale','tencent':'active'})


if __name__=='__main__':unittest.main()
