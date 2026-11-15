import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from jd_translation import TranslationStore, AIProvider, segment_plan, rebuild, translation_info, needs_translation

DETAIL = {'id':'test:1','sections':[{'heading':'Overview','text':'Build secure cloud services.\n\n• At least 3 years of experience.\n1. Collaborate with global teams.\n\n现有中文段落。'}]}


class FakeProvider:
    configured, model = True, 'test-model'
    def __init__(self): self.calls = 0
    def translate(self, items):
        self.calls += 1
        return {key:'翻译：'+value for key, value in items.items()}


class TranslationTests(unittest.TestCase):
    def test_exact_paragraphs_blanks_bullets_and_existing_chinese(self):
        items, layout = segment_plan(DETAIL)
        translated = rebuild(layout, {key:'译文' for key in items})
        self.assertEqual(translated[0]['heading'], '译文')
        self.assertEqual(translated[0]['text'], '译文\n\n• 译文\n1. 译文\n\n现有中文段落。')

    def test_missing_or_extra_newlines_rejected(self):
        items, layout = segment_plan(DETAIL)
        with self.assertRaises(KeyError): rebuild(layout,{})
        with self.assertRaises(ValueError): rebuild(layout,{key:'不应\n新增段落' for key in items})

    def test_content_changes_invalidate_translation_but_fetch_date_does_not(self):
        original = translation_info(DETAIL)
        other = copy.deepcopy(DETAIL);other['fetchedAt']='tomorrow'
        self.assertEqual(translation_info(other)['translationPath'], original['translationPath'])
        other['sections'][0]['text'] += '\nNew qualification.'
        self.assertNotEqual(translation_info(other)['translationPath'], original['translationPath'])

    def test_chinese_jd_not_translated_again(self):
        self.assertFalse(needs_translation('我们招聘软件工程师，要求熟悉 Python、SQL 和云平台。'*20))
        self.assertTrue(translation_info(DETAIL)['translationEligible'])

    def test_saved_translation_reused_without_provider_call(self):
        with tempfile.TemporaryDirectory() as root:
            provider=FakeProvider();store=TranslationStore(provider,root)
            try:
                store.start(DETAIL);store.pool.shutdown(wait=True)
                self.assertEqual(store.read(DETAIL)['status'],'ready')
                self.assertEqual(store.start(DETAIL)['status'],'ready')
                self.assertEqual(provider.calls,1)
                changed=copy.deepcopy(DETAIL);changed['sections'][0]['text']+=' More details.'
                self.assertEqual(store.read(changed)['status'],'available')
            finally: store.pool.shutdown(wait=True)

    def test_failure_not_written_as_translation(self):
        with tempfile.TemporaryDirectory() as root:
            provider=FakeProvider()
            def fail(_): raise ValueError('provider error with sensitive details')
            provider.translate=fail
            store=TranslationStore(provider,root);store.start(DETAIL);store.pool.shutdown(wait=True)
            status=store.read(DETAIL)
            self.assertEqual(status['status'],'error')
            self.assertNotIn('sensitive',str(status))

    def test_provider_rejects_changed_requirements_and_truncation(self):
        class Response:
            def __init__(self,data): self.raw=json.dumps(data).encode()
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self):return self.raw
        provider=AIProvider();provider.key='test-key';provider.base='https://example.com'
        bad={'stop_reason':'end_turn','content':[{'type':'text','text':'{"0":"至少 5 年经验"}'}]}
        with patch('jd_translation.urllib.request.urlopen',return_value=Response(bad)):
            with self.assertRaises(ValueError):provider.translate({'0':'At least 3 years of experience'})
        bad['stop_reason']='max_tokens'
        with patch('jd_translation.urllib.request.urlopen',return_value=Response(bad)):
            with self.assertRaises(ValueError):provider.translate({'0':'At least 3 years of experience'})


if __name__ == '__main__': unittest.main()
