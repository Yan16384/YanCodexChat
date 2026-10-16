"""Check the bundled single BAT without network, polling, or desktop writes.

Run on Windows: python scripts/check_template.py [path-to-CodexChat.bat]
Only temporary synthetic databases and mocked subprocesses are used.
"""

from __future__ import annotations

import base64
import contextlib
import gc
import hashlib
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
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else home / 'assets' / 'CodexChat.bat'
    if not path.exists() and len(sys.argv) == 1:
        path = home / 'CodexChat.bat'
    content = path.read_bytes()
    assert b'set "TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN"' in content, 'Public template must use a token placeholder'
    assert b'set "ALLOWED_USER_ID=YOUR_TELEGRAM_USER_ID"' in content, 'Public template must use an ID placeholder'
    token_pattern = rb'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'
    assert not re.search(token_pattern, content), 'Credential found in BAT text'
    blocks: dict[str, bytes] = {}
    for name in ('BOOTSTRAP', 'PYTHON_PAYLOAD', 'NODE_PAYLOAD'):
        match = re.search(rb'(?ms)^__' + name.encode() + rb'_BEGIN\r?\n(.*?)\r?\n__' + name.encode() + rb'_END', content)
        assert match, f'Missing {name}'
        decoded = base64.b64decode(re.sub(rb'\s', b'', match[1]), validate=True)
        assert not re.search(token_pattern, decoded), f'Credential found in {name}'
        blocks[name] = decoded
    program = compile(blocks['PYTHON_PAYLOAD'], '<embedded-bridge>', 'exec')
    if sys.platform != 'win32':
        raise SystemExit('Payload checks passed. Caller-context checks require Windows (bridge imports msvcrt).')

    with tempfile.TemporaryDirectory(prefix='yancodexchat-check-') as temporary:
        root = Path(temporary) / '\u4e2d\u6587 space'
        root.mkdir()
        module = types.ModuleType('yancodexchat_template_check')
        module.__file__ = str(root / 'bridge.py')
        with patch.dict(os.environ, {'CODEX_BRIDGE_ROOT': str(root)}, clear=False):
            exec(program, module.__dict__)
        source = root / 'codex-state.sqlite'
        module.CODEX_STATE_DB = source
        chosen: list[str] = []

        class FakeProcess:
            def __init__(self, args, **kwargs):
                chosen.append(kwargs['env']['CODEX_TG_SESSION'])

        def make_db(schema: str, rows: list[tuple]) -> None:
            with contextlib.closing(sqlite3.connect(source)) as conn:
                conn.execute('DROP TABLE IF EXISTS threads')
                conn.execute(schema)
                if rows:
                    fields = ','.join('?' for _ in rows[0])
                    conn.executemany('INSERT INTO threads VALUES (' + fields + ')', rows)
                conn.commit()

        def start(session: str = '') -> None:
            with patch.object(module.shutil, 'which', return_value='node.exe'), patch.object(module.subprocess, 'Popen', FakeProcess):
                module.DesktopCodexClient(session)._start()

        # A normal new installation inherits no caller-session variable.
        env = {k: v for k, v in os.environ.items() if k != 'CODEX_TG_SESSION'}
        with patch.dict(os.environ, env, clear=True):
            make_db('CREATE TABLE threads(id TEXT, archived INTEGER, updated_at_ms INTEGER)', [('old', 0, 1), ('new', 0, 2), ('archived', 1, 9)])
            with contextlib.closing(module.db()) as binding:
                module.set_chat(binding, 123, 'saved-target', True)
                before = binding.execute('SELECT * FROM chats').fetchall()
                start()
                assert chosen[-1] == 'new', 'Cold startup did not use latest existing unarchived caller'
                assert binding.execute('SELECT * FROM chats').fetchall() == before, 'Bootstrap changed the saved Telegram binding'

            start('explicit-target')
            assert chosen[-1] == 'explicit-target', 'Explicit target was replaced'
            for column in ('updated_at', 'created_at_ms', 'created_at'):
                make_db(f'CREATE TABLE threads(id TEXT, archived INTEGER, {column} INTEGER)', [('old', 0, 1), ('new', 0, 2)])
                start()
                assert chosen[-1] == 'new', f'Unsupported timestamp variant: {column}'
            make_db('CREATE TABLE threads(id TEXT)', [('a',), ('b',)])
            start()
            assert chosen[-1] == 'b', 'ID-only schema should retain an existing caller'
            make_db('CREATE TABLE threads(id TEXT, archived INTEGER)', [('archived', 1)])
            for missing_db in (False, True):
                if missing_db:
                    module.CODEX_STATE_DB = root / 'absent.sqlite'
                attempts = len(chosen)
                try:
                    start()
                except RuntimeError as exc:
                    assert '\u672a\u627e\u5230' in str(exc) and '\u5bf9\u8bdd' in str(exc)
                else:
                    raise AssertionError('Missing existing conversation should produce an actionable error')
                assert len(chosen) == attempts, 'Started helper with empty caller ID'

        # sqlite connection transaction contexts need not close handles; let
        # discarded startup contexts release theirs before Windows cleanup.
        gc.collect()

    print(json.dumps({
        'result': 'passed',
        'checks': ['payload integrity', 'public placeholders', 'decoded credential scan', 'empty-session bootstrap', 'saved-binding preservation', 'explicit target', 'timestamp schema variants', 'ID-only schema', 'empty/missing state'],
        'bat_sha256': hashlib.sha256(content).hexdigest(),
        'not_checked': ['live desktop reads', 'Telegram message round-trip'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
