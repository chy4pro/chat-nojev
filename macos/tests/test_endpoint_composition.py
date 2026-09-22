import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from generate import _endpoint, base_has_version_segment


# #42: OPENAI_BASE_URL 的拼接规则。全部离线——不发网络请求、不读凭据（base 显式传参）。
# 上游这里还测判断层那条同源规则（jev_request_url 三种写法、JevJudge._post 走同一条、
# 完整动作路径不给列模型）；判断合进了生成那一次调用，那些函数和它们的用例一起没了。
class EndpointCompositionTests(unittest.TestCase):
    def test_generation_endpoint_unchanged(self):
        # 生成层两态规则的重构守卫：这些 URL 任何一条变了都是回归
        cases = [
            ('https://api.deepseek.com', 'openai', 'https://api.deepseek.com/v1/chat/completions'),
            ('https://api.deepseek.com/v1', 'openai', 'https://api.deepseek.com/v1/chat/completions'),
            ('https://open.bigmodel.cn/api/paas/v4', 'openai',
             'https://open.bigmodel.cn/api/paas/v4/chat/completions'),
            ('https://api.anthropic.com', 'anthropic', 'https://api.anthropic.com/v1/messages'),
            ('https://api.anthropic.com/v1', 'anthropic', 'https://api.anthropic.com/v1/messages'),
        ]
        for base, api, expect in cases:
            with self.subTest(base=base, api=api):
                self.assertEqual(_endpoint(base, api), expect)

    def test_version_segment_detection(self):
        self.assertTrue(base_has_version_segment('https://gw.example.com/v1'))
        self.assertTrue(base_has_version_segment('https://gw.example.com/v1/'))
        self.assertTrue(base_has_version_segment('https://gw.example.com/api/v4'))
        self.assertFalse(base_has_version_segment('https://api.typesafe.ai'))
        self.assertFalse(base_has_version_segment('https://gw.example.com/api'))
        self.assertFalse(base_has_version_segment(''))
        self.assertFalse(base_has_version_segment(None))


if __name__ == '__main__':
    unittest.main()
