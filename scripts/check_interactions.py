"""Offline interaction checks. No Telegram requests or desktop writes.

Run after check_template.py. Optionally pass an extracted bridge.py path.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
from pathlib import Path
import re
import sqlite3
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
        match = re.search(rb'(?ms)^__PYTHON_PAYLOAD_BEGIN\r?\n(.*?)\r?\n__PYTHON_PAYLOAD_END', bat.read_bytes())
        assert match
        code = base64.b64decode(match[1])
    with tempfile.TemporaryDirectory(prefix='yancodexchat-questions-') as temporary:
        root = Path(temporary)
        mod = types.ModuleType('question_check')
        mod.__file__ = str(root / 'bridge.py')
        with patch.dict(os.environ, {'CODEX_BRIDGE_ROOT': str(root)}):
            exec(compile(code, '<bridge>', 'exec'), mod.__dict__)
        mod.CODEX_STATE_DB = root / 'codex.sqlite'
        rollout = root / 'rollout.jsonl'
        with contextlib.closing(sqlite3.connect(mod.CODEX_STATE_DB)) as conn:
            conn.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT)')
            conn.execute('INSERT INTO threads VALUES (?,?)', ('session', str(rollout)))
            conn.commit()

        def question(call: str) -> dict:
            return {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'functions.request_user_input_async',
                'call_id': call, 'arguments': json.dumps({'questions': [{'title': 'Choose a direction', 'options': ['A', 'B', 'C', 'D', 'E', 'F']}]})}}

        def write(events: list) -> None:
            rollout.write_text('\n'.join(json.dumps(e) for e in events) + '\n', encoding='utf8')
            mod._question_cache.clear()

        class Telegram:
            def __init__(self):
                self.sent = []
                self.edits = []
            def send(self, chat, text, keyboard=None):
                self.sent.append((chat, text, keyboard))
                return len(self.sent)
            def call(self, method, data):
                self.edits.append((method, data))

        queued = []
        with patch.object(mod.QuestionBridge, '_enqueue', lambda self, *args: queued.append(args)):
            telegram = Telegram()
            bridge = mod.QuestionBridge(telegram)
            with contextlib.closing(mod.db()) as conn:
                mod.set_chat(conn, 10, 'session', True)
            write([question('call_one')])
            bridge.show(10, 20, 'session')
            keyboard = telegram.sent[-1][2]
            assert len(keyboard) == 7
            assert all(len(b['callback_data'].encode()) <= 64 for row in keyboard for b in row)
            key = keyboard[0][0]['callback_data'].split(':')[1]
            bridge.answer(10, 99, key, '0')
            assert not queued, 'Wrong owner dispatched an answer'
            with contextlib.closing(mod.db()) as conn:
                mod.set_chat(conn, 10, 'elsewhere', True)
            bridge.answer(10, 20, key, '0')
            assert not queued, 'Old session button dispatched an answer'
            with contextlib.closing(mod.db()) as conn:
                mod.set_chat(conn, 10, 'session', True)
            bridge.answer(10, 20, key, '2')
            assert len(queued) == 1
            wrapper = queued[-1][3]
            reply = json.loads(re.search(r'>\s*(.*?)\s*</', wrapper, re.S)[1])[0]
            assert json.loads(reply['questionItemId']) == ['request_user_input_async', 'call_one', 0]
            assert reply['answer'] == 'C'
            bridge.answer(10, 20, key, '3')
            assert len(queued) == 1, 'Repeated click duplicated the write'
            with contextlib.closing(mod.db()) as conn:
                conn.execute("UPDATE questions SET status='sending' WHERE key=?", (key,)); conn.commit()
            mod.QuestionBridge(telegram)
            with contextlib.closing(mod.db()) as conn:
                assert conn.execute('SELECT status FROM questions WHERE key=?', (key,)).fetchone()[0] == 'uncertain'
            assert len(queued) == 1, 'Uncertain dispatch replayed on restart'

            write([question('call_two')])
            bridge.show(10, 20, 'session')
            key = telegram.sent[-1][2][0][0]['callback_data'].split(':')[1]
            bridge.answer(10, 20, key, 'text')
            bridge.cancel(10, 20)
            assert not bridge.consume_text(10, 20, 'cancelled')
            bridge.answer(10, 20, key, 'text')
            assert bridge.consume_text(10, 20, 'My own answer')
            assert 'My own answer' in queued[-1][3]
            write([question('call_three')])
            bridge.show(10, 20, 'session')
            key = telegram.sent[-1][2][0][0]['callback_data'].split(':')[1]
            bridge.answer(10, 20, key, 'skip')
            assert '\u8df3\u8fc7' in queued[-1][3]

            write([question('call_four')])
            bridge.show(10, 20, 'session')
            key = telegram.sent[-1][2][0][0]['callback_data'].split(':')[1]
            item = mod.pending_questions('session')[0]
            native = mod.question_reply(item['item_id'], item['question'], 'desktop answer')
            write([question('call_four'), {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': native}]}}])
            assert not mod.pending_questions('session')
            count = len(queued)
            bridge.answer(10, 20, key, '0')
            assert len(queued) == count, 'Desktop-answered question was dispatched'
            assert '<send_user_message_question_reply>' not in mod.clean_history_text(native)
            assert 'desktop answer' in mod.clean_history_text(native)

            for accepted in (True, False):
                write([question('call_accepted' if accepted else 'call_unknown')])
                bridge.show(10, 20, 'session')
                key = telegram.sent[-1][2][0][0]['callback_data'].split(':')[1]
                bridge.answer(10, 20, key, '0')
                chat, user, session, prompt, queued_key = queued[-1]
                jobs = mod.queue.Queue()
                jobs.put((chat, user, prompt, queued_key))

                def fail_after_dispatch(session, prompt, progress, is_answer, on_accepted):
                    if accepted:
                        on_accepted()
                    raise RuntimeError('provider stream overloaded')

                with patch.object(mod, 'run_desktop_codex', side_effect=fail_after_dispatch):
                    bridge._worker(session, jobs, True)
                with contextlib.closing(mod.db()) as conn:
                    state = conn.execute('SELECT status FROM questions WHERE key=?', (key,)).fetchone()[0]
                assert state == ('answered' if accepted else 'uncertain')
                error_text = telegram.sent[-1][1]
                if accepted:
                    assert '\u5df2\u9001\u8fbe' in error_text and '\u53ef\u80fd\u5df2\u9001\u8fbe' not in error_text
                else:
                    assert '\u53ef\u80fd\u5df2\u9001\u8fbe' in error_text
        print('PASS: six options, compact callbacks, owner and session checks, original IDs, duplicate prevention, text/cancel/skip, desktop answers, restart uncertainty, readable history')
        print('PASS: accepted answers retain delivered state after model failure; unconfirmed submissions remain uncertain')


if __name__ == '__main__':
    main()
