"""HUD-to-HTTP acceptance: synthetic conversations, local server, no user data."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_hud_reply as fixtures
import test_settings as network
import chat_context
import generate
import questions
import styles


class ContextRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        network.SettingsNetwork.setUpClass()

    @classmethod
    def tearDownClass(cls):
        network.SettingsNetwork.tearDownClass()

    def test_hud_all_paths_use_identical_bounded_context_on_wire(self):
        fixture = fixtures.HudReplyTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        h = fixture.h
        with tempfile.TemporaryDirectory() as directory, patch('generate.load_credentials', return_value=(
                network.SettingsNetwork.base, 'synthetic-key', 'synthetic-model', 'test', 'openai')), \
                patch('userconfig.get', return_value=''):
            h.conversations = chat_context.Conversations(Path(directory) / 'chats.json')
            h.configure_context(True, '20')
            h.save_background('chat', '合成背景：小王负责验收')
            msgs = fixture.read([fixtures.block('合成先前消息', .40, .80, .15),
                                 fixtures.block('合成当前消息', .40, .60, .15)])
            tone = styles.DEFAULT_SLOTS[0]
            h.slot_tones = [tone]
            h.generator = generate.Generator()
            # 上游这里还要装一个判断层客户端（JevJudge）并让服务端同时返回 answers：判断、
            # 排序各是一次请求，也要一起核对上下文。判断合进了生成那一次调用，所以只有
            # 这一种请求，answers 那一坨没有对应物了。
            network.Server.code = 200
            network.Server.response = {'choices': [{'message': {'content': '1. 合成候选甲\n2. 合成候选乙'}}]}
            network.Server.requests = []
            context = h._pregen_req[1]
            # Resident loops execute real adapters, with only their wait boundary replaced.
            class StopLoop(BaseException):
                pass
            signal = Mock()
            signal.wait.side_effect = [None, StopLoop()]
            h._pregen_event = signal
            h._pregen_req = (msgs[-1].text, context, (tone,), h._reply_epoch)
            with self.assertRaises(StopLoop):
                h._pregen_loop()
            h._pregen_result = None
            h._reply_task(h._reply_epoch, h._analyze, msgs[-1], msgs, '')
            h._reply_task(h._reply_epoch, h._regen_work, msgs[-1].text, [tone])
            h._reply_task(h._reply_epoch, h._regenerate_work, msgs[-1].text, [tone])
            requests = list(network.Server.requests)
            # 上游这里是 11 次（预判 1 + 早跑 1 + 分析 3 + 预判命中那半路 2 + 换话术 2 +
            # 重新生成 2）。合成一次之后每条路只剩一次：早跑 1 + 分析 1 + 换话术 1 + 重新生成 1。
            self.assertGreaterEqual(len(requests), 4)
            for path, _headers, body in requests:
                self.assertEqual(path, '/v1/chat/completions')
                text = body['messages'][0]['content']
                self.assertIn(context, text)
                self.assertEqual(text.count('合成当前消息'), 1)
                self.assertEqual(text.count('合成先前消息'), 1)
                # 判断题跟着同一次请求走：题面就在这条 prompt 里，没有第二个端点
                self.assertIn(questions.render_judgment_spec(), text)
            # HTTP bodies may echo every private field. Neither HUD logs nor errors may do so.
            network.Server.code = 500
            network.Server.response = {'error': '合成隐私标记：消息、背景、候选'}
            logs = []
            with patch.dict(fixtures.HUD, {'_log': logs.append}):
                h._reply_task(h._reply_epoch, h._regen_work, msgs[-1].text, [tone])
                h._reply_task(h._reply_epoch, h._regenerate_work, msgs[-1].text, [tone])
            self.assertNotIn('合成隐私标记', str(logs) + str(fixture.queue))

    # 上游这里还有 test_local_fallback_forwards_same_context_to_judgment_and_rank：
    # judge.FallbackJudge（云端 Jev 挂了切本地 decider-2b）要把同一份上下文转给判断和排序。
    # 判断层整条都删了，那个类和它唯一的被测行为一起不存在。


if __name__ == '__main__':
    unittest.main()
