"""Check desktop creation lifecycle with isolated state and fake transport.

No Telegram requests, desktop writes, or model calls. Optional bridge.py argument.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
from unittest.mock import patch


def main() -> None:
    home = Path(__file__).resolve().parents[1]
    if len(sys.argv) > 1:
        code = Path(sys.argv[1]).read_bytes()
    else:
        bat = home / 'assets' / 'CodexChat.bat'
        if not bat.exists():
            bat = home / 'CodexChat.bat'
        code = base64.b64decode(re.search(rb'(?ms)^__PYTHON_PAYLOAD_BEGIN\r?\n(.*?)\r?\n__PYTHON_PAYLOAD_END', bat.read_bytes())[1])
    with tempfile.TemporaryDirectory(prefix='yancodexchat-create-') as temp:
        mod = types.ModuleType('new_chat_check')
        mod.__file__ = str(Path(temp) / 'bridge.py')
        with patch.dict(os.environ, {'CODEX_BRIDGE_ROOT': temp}):
            exec(compile(code, '<bridge>', 'exec'), mod.__dict__)

        class Telegram:
            def __init__(self):
                self.sent = []
            def send(self, chat, text, keyboard=None):
                self.sent.append((chat, text, keyboard))
            def call(self, *args):
                return {}

        telegram = Telegram()
        queued = []
        calls = []
        outcome = {'threadId': 'created-thread', 'hostId': 'local'}

        def desktop_call(client, tool, arguments):
            calls.append((tool, arguments))
            assert tool == 'create_thread'
            assert arguments == {'prompt': 'first prompt', 'target': {'type': 'projectless'}}
            if isinstance(outcome, Exception):
                raise outcome
            return {'result': {'contentItems': [{'text': json.dumps(outcome)}]}}

        def binding():
            with contextlib.closing(mod.db()) as conn:
                return conn.execute('SELECT session_id,active FROM chats WHERE chat_id=10').fetchone()

        def state(key):
            with contextlib.closing(mod.db()) as conn:
                return conn.execute('SELECT status FROM new_chats WHERE key=?', (key,)).fetchone()[0]

        with patch.object(mod.DesktopCodexClient, 'call', desktop_call), \
             patch.object(mod.QuestionBridge, '_enqueue', lambda self, *args: queued.append(args)), \
             patch.object(mod.threading.Thread, 'start', lambda self: None), \
             patch.object(mod, 'session_label', lambda session: session):
            interactions = mod.QuestionBridge(telegram)
            flow = mod.NewChatBridge(telegram, interactions)
            with contextlib.closing(mod.db()) as conn:
                mod.set_chat(conn, 10, 'original', True)
            flow.begin(10, 20, 'success')
            assert not calls and binding() == ('original', 1)
            flow.begin(10, 20, 'success')
            flow.consume(10, 99, 'wrong user')
            assert state('success') == 'draft'
            assert flow.consume(10, 20, 'first prompt')
            assert state('success') == 'creating'
            flow.consume(10, 20, 'duplicate')
            flow._create('success', 10, 20, 'first prompt')
            assert len(calls) == 1 and binding() == ('created-thread', 1)
            assert queued == [(10, 20, 'created-thread', None, None)], 'Initial prompt was resent'
            assert state('success') == 'created'
            flow.begin(10, 20, 'success')
            assert not flow.consume(10, 20, 'not another creation')

            # A cancelled draft cannot create; a submitted creation can finish
            # but may not overwrite a newer selection.
            flow.begin(10, 20, 'cancel-draft')
            flow.cancel(10, 20)
            assert state('cancel-draft') == 'cancelled'
            assert not flow.consume(10, 20, 'cancelled prompt')
            flow.begin(10, 20, 'switch')
            flow.consume(10, 20, 'first prompt')
            flow.cancel(10, 20)
            with contextlib.closing(mod.db()) as conn:
                mod.set_chat(conn, 10, 'switched-target', True)
            flow._create('switch', 10, 20, 'first prompt')
            assert binding() == ('switched-target', 1) and len(queued) == 1

            for key, result, expected in [('pending', {'clientThreadId': 'setup-only'}, 'pending'),
                                          ('timeout', RuntimeError('connection timeout'), 'uncertain'),
                                          ('empty', {}, 'uncertain')]:
                outcome = result
                flow.begin(10, 20, key)
                flow.consume(10, 20, 'first prompt')
                flow._create(key, 10, 20, 'first prompt')
                assert state(key) == expected
                assert binding() == ('switched-target', 1) and len(queued) == 1
            count = len(calls)
            flow.begin(10, 20, 'restart')
            flow.consume(10, 20, 'first prompt')
            mod.NewChatBridge(telegram, interactions)
            assert state('restart') == 'uncertain' and len(calls) == count

            # Initial reply observation has no write and stays on the first
            # turn even if a later desktop-only turn becomes visible.
            observed = mod.DesktopCodexClient('created-thread')
            initial = {'id': 'first', 'status': 'completed', 'items': [
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'first reply'}]}
            other = {'id': 'desktop-only', 'status': 'completed', 'items': [
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'do not forward'}]}
            with patch.object(observed, 'read', return_value={'turns': [other, initial]}), \
                 patch.object(mod.time, 'sleep', lambda _: None):
                assert observed.wait_reply('created-thread', initial=True) == 'first reply'
                assert observed.reply_turn_id == 'first'
            initial['status'] = 'failed'
            initial['error'] = 'provider overloaded'
            with patch.object(observed, 'read', return_value={'turns': [initial]}), \
                 patch.object(mod.time, 'sleep', lambda _: None):
                try:
                    observed.wait_reply('created-thread', initial=True)
                except RuntimeError as exc:
                    assert 'overloaded' in str(exc)
                else:
                    raise AssertionError('Failed turn returned partial text as a final answer')
    print('PASS: menu, draft, owner, single create, initial prompt once, cancel/switch, pending IDs, unknown result, restart no replay, initial reply, model failure')


if __name__ == '__main__':
    main()
