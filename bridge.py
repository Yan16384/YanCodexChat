from __future__ import annotations

import json
import msvcrt
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / 'runtime'
DATA_DIR = ROOT / 'data'
MEDIA_DIR = ROOT / 'telegram_media'
DB_PATH = DATA_DIR / 'bridge.sqlite3'
LOCK_PATH = RUNTIME_DIR / 'bridge.lock'
PID_PATH = RUNTIME_DIR / 'bridge.pid'
HEALTH_PATH = RUNTIME_DIR / 'health.json'
CODEX_STATE_DB = Path.home() / '.codex' / 'state_5.sqlite'


def load_config() -> dict[str, Any]:
    raw_ids = os.environ.get('ALLOWED_USER_IDS', '').strip() or os.environ.get('ALLOWED_USER_ID', '').strip()
    # Keep the old names readable for existing local installations. New
    # deployments only need the two variables exposed by the BAT file.
    raw_ids = raw_ids or os.environ.get('CODEX_TG_USER_IDS', '').strip()
    ids = [value.strip() for value in raw_ids.split(',') if value.strip()]
    session = os.environ.get('CODEX_TG_SESSION', '').strip()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip() or os.environ.get('CODEX_TG_TOKEN', '').strip()
    if not token or not ids:
        raise RuntimeError('BAT 顶部必须填写 TELEGRAM_BOT_TOKEN 和 ALLOWED_USER_ID。')
    return {'allowed_user_ids': ids, 'default_session_id': session, 'telegram_bot_token': token}


def write_health(status: str, stage: str, reason: str = '', **details: Any) -> None:
    """Write a small local health record without ever including the bot token."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        'status': status,
        'stage': stage,
        'pid': os.getpid(),
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
    if reason:
        payload['reason'] = reason[:1000]
    payload.update(details)
    HEALTH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def acquire_instance_lock() -> Any | None:
    lock = LOCK_PATH.open('a+', encoding='ascii')
    lock.seek(0, os.SEEK_END)
    if lock.tell() == 0:
        lock.write('0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None
    return lock


class Telegram:
    def __init__(self, token: str):
        self.base = f'https://api.telegram.org/bot{token}'

    def call(self, method: str, data: dict[str, Any] | None = None) -> Any:
        body = urlencode(data or {}).encode()
        req = Request(f'{self.base}/{method}', data=body, method='POST')
        with urlopen(req, timeout=70) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not payload.get('ok'):
            raise RuntimeError(f"Telegram API {method} failed: {payload}")
        return payload.get('result')

    def send(self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> None:
        for start in range(0, len(text), 3800):
            data: dict[str, Any] = {'chat_id': chat_id, 'text': text[start:start + 3800]}
            if keyboard and start == 0:
                data['reply_markup'] = json.dumps({'inline_keyboard': keyboard}, ensure_ascii=False)
            self.call('sendMessage', data)

    def download_file(self, file_id: str, destination: Path) -> Path:
        info = self.call('getFile', {'file_id': file_id}) or {}
        file_path = str(info.get('file_path') or '').strip()
        if not file_path:
            raise RuntimeError('Telegram 没有返回图片文件路径。')
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f'{self.base.rsplit("/bot", 1)[0]}/file/bot{self.base.rsplit("/bot", 1)[1]}/{quote(file_path)}'
        with urlopen(Request(url), timeout=120) as response:
            destination.write_bytes(response.read())
        return destination


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        direction TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(chats)')}
    if 'active' not in columns:
        conn.execute('ALTER TABLE chats ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
    conn.commit()
    return conn


def saved_offset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM state WHERE key='telegram_offset'").fetchone()
    return int(row[0]) if row else 0


def save_offset(conn: sqlite3.Connection, offset: int) -> None:
    conn.execute(
        "INSERT INTO state(key,value) VALUES ('telegram_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(offset),),
    )
    conn.commit()


def initialize_offset(conn: sqlite3.Connection, telegram: Telegram) -> int:
    row = conn.execute("SELECT value FROM state WHERE key='telegram_offset'").fetchone()
    if row:
        return int(row[0])
    # Discard updates left by older bridge instances. The next user message
    # will be fetched after this point.
    pending = telegram.call('getUpdates', {
        'timeout': 0,
        'offset': -1,
        'allowed_updates': '["message", "callback_query"]',
    }) or []
    offset = max((int(item['update_id']) + 1 for item in pending), default=0)
    save_offset(conn, offset)
    return offset


def session_for(conn: sqlite3.Connection, chat_id: int, default: str) -> str:
    row = conn.execute('SELECT session_id FROM chats WHERE chat_id=?', (chat_id,)).fetchone()
    if row:
        return row[0]
    if not default:
        recent = codex_sessions(False, 1)
        default = recent[0]['id'] if recent else ''
    if not default:
        raise RuntimeError('没有找到可绑定的 Codex 会话，请先在电脑 Codex 中打开一个对话。')
    conn.execute('INSERT INTO chats(chat_id, session_id) VALUES (?, ?)', (chat_id, default))
    conn.commit()
    return default


def set_chat(conn: sqlite3.Connection, chat_id: int, session_id: str, active: bool) -> None:
    conn.execute(
        '''INSERT INTO chats(chat_id, session_id, active, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id,
           active=excluded.active, updated_at=CURRENT_TIMESTAMP''',
        (chat_id, session_id, int(active)),
    )
    conn.commit()


def chat_active(conn: sqlite3.Connection, chat_id: int) -> bool:
    row = conn.execute('SELECT active FROM chats WHERE chat_id=?', (chat_id,)).fetchone()
    return bool(row and row[0])


def codex_sessions(pinned: bool = False, limit: int | None = 10) -> list[dict[str, Any]]:
    if not CODEX_STATE_DB.exists():
        return []
    try:
        source = sqlite3.connect(f'file:{CODEX_STATE_DB}?mode=ro', uri=True, timeout=2)
        order = 'COALESCE(updated_at_ms, updated_at, created_at_ms, created_at) DESC'
        if pinned:
            # Current Codex stores pinned chats in the Pinned section; older
            # versions used the is_pinned flag directly.
            where = "WHERE archived=0 AND (is_pinned=1 OR thread_section_id IN (SELECT id FROM thread_sections WHERE lower(name)='pinned'))"
        else:
            where = 'WHERE archived=0'
        suffix = f' LIMIT {int(limit)}' if limit else ''
        rows = source.execute(
            f'''SELECT id, name, title, first_user_message, preview, is_pinned,
                       COALESCE(updated_at_ms, updated_at, created_at_ms, created_at)
                FROM threads {where} ORDER BY {order}{suffix}'''
        ).fetchall()
        source.close()
    except sqlite3.Error:
        return []
    result = []
    for session_id, name, title, first, preview, is_pinned, stamp in rows:
        label = (name or title or first or preview or '未命名对话').strip()
        if '## My request:' in label:
            label = label.split('## My request:', 1)[1].strip()
        label = re.sub(r'\s+', ' ', label).replace('```', '')
        if label.startswith('# Files mentioned by the user:'):
            label = '未命名对话'
        if len(label) > 54:
            label = label[:54] + '...'
        try:
            stamp_value = float(stamp or 0)
            if stamp_value > 10_000_000_000:
                stamp_value /= 1000
            when = datetime.fromtimestamp(stamp_value).strftime('%m-%d %H:%M') if stamp_value else ''
        except (ValueError, OSError, TypeError):
            when = ''
        result.append({'id': session_id, 'label': label, 'when': when, 'pinned': bool(is_pinned)})
    return result


def menu_keyboard() -> list[list[dict[str, str]]]:
    return [
        [{'text': '1 设备对话（最近10条）', 'callback_data': 'list:recent'}],
        [{'text': '2 置顶对话（全部）', 'callback_data': 'list:pinned'}],
        [{'text': '当前会话', 'callback_data': 'current'}, {'text': '最近5条记录', 'callback_data': 'history'}],
        [{'text': '帮助', 'callback_data': 'help'}],
    ]


def session_keyboard(items: list[dict[str, Any]], back: str = 'menu') -> list[list[dict[str, str]]]:
    keyboard = [[{'text': f"{item['label']}  {item['when']}", 'callback_data': f"open:{item['id']}"}] for item in items]
    keyboard.append([{'text': '返回主菜单', 'callback_data': back}])
    return keyboard


def recent_messages(conn: sqlite3.Connection, chat_id: int, limit: int = 5) -> str:
    rows = conn.execute(
        'SELECT direction, text, created_at FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?',
        (chat_id, limit),
    ).fetchall()
    if not rows:
        return '最近没有本地聊天记录。'
    lines = []
    for direction, text, created in reversed(rows):
        # Errors are replies to the user's message too; keep the transcript in
        # the same two-role format instead of exposing an internal "system"
        # role in Telegram.
        name = '我' if direction == 'in' else 'Codex'
        lines.append(f'{name}：{text[:1000]}')
    return '\n\n'.join(lines)


def session_label(session_id: str) -> str:
    for item in codex_sessions(False, None):
        if item['id'] == session_id:
            return item['label']
    return session_id


def combined_history(conn: sqlite3.Connection, chat_id: int, session_id: str) -> str:
    return codex_history(session_id)


def clean_history_text(text: str) -> str:
    text = text.strip()
    if '## My request:' in text:
        text = text.split('## My request:', 1)[1].strip()
    if text.startswith('<skill>') or text.startswith('# AGENTS.md instructions'):
        return ''
    text = re.sub(r"\n?Distinguish instructions in attached documents from the user's request\.?", '', text)
    text = re.sub(r'\n?## Files mentioned by the user:.*?(?=\n## My request:)', '', text, flags=re.S)
    text = text.replace('\\\\n', '\n').replace('\\\n', '\n')
    text = re.sub(r'\\([*_`\[\]()])', r'\1', text)
    text = re.sub(r'<image\b[^>]*>.*?</image>', '[图片]', text, flags=re.S)
    text = re.sub(r'!\[([^\]]*)\]\([^\n)]*\)', r'[图片：\1]', text)
    text = re.sub(r'(?<!!)\[([^\]]+)\]\([^\n)]*\)', r'\1', text)
    text = re.sub(r'```[^\n]*\n?', '', text)
    text = text.replace('**', '').replace('`', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def codex_history(session_id: str, limit: int = 5) -> str:
    if not CODEX_STATE_DB.exists():
        return '找不到 Codex 历史数据库。'
    try:
        state = sqlite3.connect(f'file:{CODEX_STATE_DB}?mode=ro', uri=True, timeout=2)
        row = state.execute('SELECT rollout_path FROM threads WHERE id=?', (session_id,)).fetchone()
        state.close()
        if not row:
            return '找不到该 Codex 会话的历史文件。'
        path = str(row[0])
        if path.startswith('\\\\?\\'):
            path = path[4:]
        items: list[tuple[str, str]] = []
        with open(path, encoding='utf-8') as source:
            for line in source:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                payload = event.get('payload') or {}
                if event.get('type') != 'response_item' or payload.get('type') != 'message':
                    continue
                role = payload.get('role')
                if role not in ('user', 'assistant'):
                    continue
                if role == 'assistant' and payload.get('phase', payload.get('channel')) not in (None, 'final_answer', 'final'):
                    continue
                parts = []
                for content in payload.get('content') or []:
                    if isinstance(content, dict) and isinstance(content.get('text'), str):
                        parts.append(content['text'])
                text = clean_history_text('\n'.join(parts))
                if text:
                    items.append(('我' if role == 'user' else 'Codex', text))
        if not items:
            return '该 Codex 会话暂无可显示的聊天记录。'
        turns: list[tuple[str, list[str]]] = []
        for role, text in items:
            if role == '我':
                turns.append((text, []))
            elif turns:
                # Some rollout snapshots contain several assistant messages
                # for a turn. The last final reply is the transcript answer.
                turns[-1][1][:] = [text]
        if not turns:
            return '该 Codex 会话暂无可显示的用户消息。'
        blocks = []
        for user_text, replies in turns[-limit:]:
            user_preview = user_text if len(user_text) <= 200 else user_text[:200] + '…（后文省略）'
            block = f'我：{user_preview}'
            if replies:
                answer = replies[-1]
                answer = answer if len(answer) <= 350 else answer[:350] + '…（后文省略）'
                block += '\n\nCodex：' + answer
            else:
                block += '\n\nCodex：（尚无最终回复）'
            blocks.append(block)
        return '\n\n'.join(blocks)
    except (OSError, sqlite3.Error, UnicodeError) as exc:
        return f'读取 Codex 会话历史失败：{exc}'


def help_text() -> str:
    return ('发送普通文字继续当前 Codex 对话。\n'
            '/start 打开菜单\n'
            '/stop 退出当前会话\n'
            '/status 查看当前会话\n'
            '/history 查看当前 Codex 会话最近5组问答\n'
            '/bind SESSION_ID 手动绑定会话\n'
            '/use SESSION_ID 直接进入会话（推荐）')


class DesktopCodexClient:
    """Call the already-running Codex desktop app through its local app-tools pipe."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.helper = ROOT / 'app_tools_client.cjs'
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def _start(self) -> None:
        if self._process and self._process.poll() is None:
            return
        node = shutil.which('node.exe') or shutil.which('node')
        if not node:
            raise RuntimeError('找不到 Node.js，无法连接 Codex 桌面本地接口。')
        environment = os.environ.copy()
        environment['CODEX_TG_SESSION'] = self.session_id
        self._process = subprocess.Popen(
            [node, str(self.helper)], cwd=str(ROOT), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding='utf-8', errors='replace', bufsize=1,
            env=environment,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._start()
            assert self._process is not None
            if self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError('Codex 桌面本地客户端没有建立输入输出通道。')
            try:
                self._process.stdin.write(json.dumps({
                    'tool': tool, 'arguments': arguments,
                }, ensure_ascii=False) + '\n')
                self._process.stdin.flush()
                line = self._process.stdout.readline()
            except (OSError, BrokenPipeError) as exc:
                self._process = None
                raise RuntimeError(f'连接 Codex 桌面本地接口失败：{exc}') from exc
            if not line:
                self._process = None
                raise RuntimeError('Codex 桌面本地客户端已退出。')
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f'Codex 桌面本地接口返回无效数据：{line[:500]}') from exc
            if not result.get('ok'):
                raise RuntimeError(str(result.get('error') or 'Codex 桌面本地接口调用失败'))
            return result.get('response') or {}

    @staticmethod
    def _payload(response: dict[str, Any]) -> dict[str, Any]:
        result = response.get('result') or {}
        for item in result.get('contentItems') or []:
            if isinstance(item, dict) and isinstance(item.get('text'), str):
                try:
                    return json.loads(item['text'])
                except json.JSONDecodeError:
                    continue
        return {}

    def read(self, session_id: str) -> dict[str, Any]:
        response = self.call('read_thread', {
            'threadId': session_id,
            'turnLimit': 2,
            'includeOutputs': True,
            'maxOutputCharsPerItem': 12000,
        })
        return self._payload(response)

    def probe(self) -> dict[str, Any]:
        """Verify the desktop app-tools pipe before declaring startup healthy."""
        return self._payload(self.call('list_threads', {'limit': 1}))

    @staticmethod
    def _turns(payload: dict[str, Any]) -> list[dict[str, Any]]:
        turns = payload.get('turns')
        return turns if isinstance(turns, list) else []

    @staticmethod
    def _status(turn: dict[str, Any]) -> str:
        status = turn.get('status')
        if isinstance(status, dict):
            status = status.get('type')
        return str(status or '').lower()

    @staticmethod
    def _reply(turn: dict[str, Any]) -> str:
        messages: list[tuple[str, str]] = []
        for item in turn.get('items') or []:
            if not isinstance(item, dict) or item.get('type') != 'agentMessage':
                continue
            text = item.get('text')
            if isinstance(text, str) and text.strip():
                messages.append((str(item.get('phase') or ''), text.strip()))
        if not messages:
            return ''
        for phase, text in reversed(messages):
            if phase in ('final_answer', 'final'):
                return text
        return messages[-1][1]

    def send(self, session_id: str, prompt: str) -> str:
        before = self.read(session_id)
        previous_ids = {str(turn.get('id')) for turn in self._turns(before)}
        self.call('send_message_to_thread', {
            'threadId': session_id,
            'prompt': prompt,
        })
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            time.sleep(1.0)
            payload = self.read(session_id)
            turns = self._turns(payload)
            if not turns:
                continue
            turn = turns[0]
            turn_id = str(turn.get('id') or '')
            if turn_id in previous_ids and len(previous_ids) > 0:
                continue
            status = self._status(turn)
            if status in ('inprogress', 'in_progress', 'running', 'started', 'queued', 'pending', 'active'):
                continue
            reply = self._reply(turn)
            if reply:
                return reply
            error = turn.get('error')
            if error:
                raise RuntimeError(str(error))
            if status in ('failed', 'error', 'interrupted', 'cancelled', 'canceled'):
                raise RuntimeError(f'Codex 桌面会话状态：{status}')
        raise RuntimeError('Codex 桌面回复超时，任务可能仍在电脑端运行。')


_desktop_client: DesktopCodexClient | None = None


def run_desktop_codex(session_id: str, prompt: str) -> str:
    global _desktop_client
    if _desktop_client is None or _desktop_client.session_id != session_id:
        _desktop_client = DesktopCodexClient(session_id)
    return _desktop_client.send(session_id, prompt)


def incoming_message_text(message: dict[str, Any], telegram: Telegram, update_id: int) -> str:
    """Convert a Telegram text or photo message into a Codex prompt."""
    text = str(message.get('text') or '').strip()
    photos = message.get('photo') or []
    if not photos:
        return text
    largest = photos[-1] if isinstance(photos[-1], dict) else {}
    file_id = str(largest.get('file_id') or '').strip()
    if not file_id:
        raise RuntimeError('Telegram 图片消息缺少 file_id。')
    caption = str(message.get('caption') or '').strip()
    destination = MEDIA_DIR / f'{update_id}_{file_id[:24]}.jpg'
    downloaded = telegram.download_file(file_id, destination)
    prompt_parts = [
        '这条 Telegram 消息包含一张图片。请使用本地图片查看能力读取图片内容。',
        f'图片文件路径：{downloaded}',
    ]
    if caption:
        prompt_parts.append(f'图片配文：\n{caption}')
    else:
        prompt_parts.append('图片没有配文，请直接分析图片内容。')
    return '\n\n'.join(prompt_parts)


def main() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        config = load_config()
        instance_lock = acquire_instance_lock()
        if instance_lock is None:
            print('服务已经在运行，未启动第二个实例。')
            return
        PID_PATH.write_text(str(os.getpid()), encoding='ascii')
        allowed = {int(value) for value in config['allowed_user_ids']}
        default_session = config['default_session_id']
        telegram = Telegram(config['telegram_bot_token'])
        conn = db()
        me = telegram.call('getMe')
        desktop = DesktopCodexClient(default_session)
        desktop.probe()
        write_health('ready', 'health_check', telegram_bot=me.get('username', ''),
                     allowed_user_count=len(allowed), codex='app-tools')
        print(f"Telegram bridge started as @{me.get('username', '')}")
        print(f"Allowed user count: {len(allowed)}")
        offset = initialize_offset(conn, telegram)
        while True:
            try:
                updates = telegram.call('getUpdates', {
                    'timeout': 50,
                    'offset': offset,
                    'allowed_updates': '["message", "callback_query"]',
                })
                for update in updates or []:
                    offset = max(offset, int(update['update_id']) + 1)
                    save_offset(conn, offset)
                    callback = update.get('callback_query')
                    if callback:
                        sender = callback.get('from') or {}
                        user_id = int(sender.get('id', 0))
                        callback_message = callback.get('message') or {}
                        chat_id = int((callback_message.get('chat') or {}).get('id', 0))
                        telegram.call('answerCallbackQuery', {'callback_query_id': callback.get('id', '')})
                        if user_id not in allowed or not chat_id:
                            continue
                        action = str(callback.get('data') or '')
                        if action in ('menu', 'help'):
                            telegram.send(chat_id, 'Codex Chat 主菜单\n请选择要执行的操作：', menu_keyboard())
                        elif action == 'list:recent':
                            items = codex_sessions(False, 10)
                            text = '设备对话（最近10条）：\n点击一个会话进入。' if items else '没有找到设备对话。'
                            telegram.send(chat_id, text, session_keyboard(items, 'menu'))
                        elif action == 'list:pinned':
                            items = codex_sessions(True, None)
                            text = '置顶对话：\n点击一个会话进入。' if items else '当前没有置顶对话。'
                            telegram.send(chat_id, text, session_keyboard(items, 'menu'))
                        elif action == 'current':
                            session = session_for(conn, chat_id, default_session)
                            state = '已进入' if chat_active(conn, chat_id) else '未进入'
                            telegram.send(chat_id, f'{state}会话：{session_label(session)}\n会话 ID：{session}\n\n{combined_history(conn, chat_id, session)}', menu_keyboard())
                        elif action == 'history':
                            session = session_for(conn, chat_id, default_session)
                            telegram.send(chat_id, combined_history(conn, chat_id, session), menu_keyboard())
                        elif action.startswith('open:'):
                            session = action[5:].strip()
                            set_chat(conn, chat_id, session, True)
                            label = session_label(session)
                            telegram.send(chat_id, f'已进入「{label}」对话。\n会话 ID：{session}\n若要退出当前会话，请发送 /stop。\n\n{combined_history(conn, chat_id, session)}')
                        continue
                    message = update.get('message') or {}
                    sender = message.get('from') or {}
                    user_id = int(sender.get('id', 0))
                    chat_id = int((message.get('chat') or {}).get('id', 0))
                    if not chat_id:
                        continue
                    if user_id not in allowed:
                        telegram.send(chat_id, '此机器人未授权给你的账号使用。')
                        continue
                    try:
                        text = incoming_message_text(message, telegram, int(update['update_id']))
                    except Exception as exc:
                        telegram.send(chat_id, f'图片接收失败：{exc}')
                        continue
                    if not text:
                        continue
                    if text in ('/start', '/help'):
                        current = session_for(conn, chat_id, default_session)
                        telegram.send(chat_id, f'当前绑定：{session_label(current)}\n会话 ID：{current}\n\nCodex Chat 主菜单\n请选择要执行的操作：', menu_keyboard())
                        continue
                    if text == '/stop':
                        set_chat(conn, chat_id, session_for(conn, chat_id, default_session), False)
                        telegram.send(chat_id, '已退出当前 Codex 会话。\n发送 /start 重新选择会话。', menu_keyboard())
                        continue
                    if text == '/status':
                        session = session_for(conn, chat_id, default_session)
                        count = conn.execute('SELECT COUNT(*) FROM messages WHERE chat_id=?', (chat_id,)).fetchone()[0]
                        state = '已进入' if chat_active(conn, chat_id) else '未进入'
                        telegram.send(chat_id, f'当前 Codex 会话：{session}\n状态：{state}\n本地记录：{count} 条', menu_keyboard())
                        continue
                    if text == '/history':
                        session = session_for(conn, chat_id, default_session)
                        telegram.send(chat_id, combined_history(conn, chat_id, session), menu_keyboard())
                        continue
                    if text == '/bind' or text.startswith('/bind '):
                        session = text[5:].strip()
                        if not session:
                            telegram.send(chat_id, '用法：/bind SESSION_ID')
                        else:
                            conn.execute('INSERT INTO chats(chat_id, session_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id, updated_at=CURRENT_TIMESTAMP', (chat_id, session))
                            conn.commit()
                            set_chat(conn, chat_id, session, True)
                            telegram.send(chat_id, f'已进入「{session_label(session)}」对话。\n会话 ID：{session}\n若要退出当前会话，请发送 /stop。\n\n{combined_history(conn, chat_id, session)}')
                        continue
                    if text == '/use' or text.startswith('/use '):
                        session = text[4:].strip()
                        if not session:
                            telegram.send(chat_id, '用法：/use SESSION_ID')
                        else:
                            set_chat(conn, chat_id, session, True)
                            telegram.send(chat_id, f'已确认绑定：{session_label(session)}\n会话 ID：{session}\n\n{combined_history(conn, chat_id, session)}')
                        continue
                    session = session_for(conn, chat_id, default_session)
                    if not chat_active(conn, chat_id):
                        telegram.send(chat_id, '当前没有进入 Codex 会话，请先发送 /start 并点击一个会话。', menu_keyboard())
                        continue
                    conn.execute('INSERT INTO messages(chat_id,user_id,direction,text) VALUES (?,?,?,?)', (chat_id, user_id, 'in', text))
                    conn.commit()
                    try:
                        reply = run_desktop_codex(session, text)
                        direction = 'out'
                    except Exception as exc:
                        detail = str(exc)
                        if '正在持有这个会话的写入锁' in detail or 'already has an active writer' in detail or 'thread-store conflict' in detail:
                            reply = ('桌面 Codex 当前正在处理上一条消息，暂时无法写入新消息。\n'
                                     '请等当前回复完成后再发送；本条消息已保存在桥接记录中。')
                        else:
                            reply = f'桌面 Codex 调用失败：{detail}'
                        direction = 'error'
                    conn.execute('INSERT INTO messages(chat_id,user_id,direction,text) VALUES (?,?,?,?)', (chat_id, user_id, direction, reply))
                    conn.commit()
                    telegram.send(chat_id, reply)
            except KeyboardInterrupt:
                print('Bridge stopped.')
                return
            except Exception as exc:
                print(f'Loop error: {exc}')
                time.sleep(5)
    except Exception as exc:
        write_health('failed', 'startup', str(exc))
        print(f'启动失败：{exc}')
    finally:
        try:
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == '__main__':
    main()
