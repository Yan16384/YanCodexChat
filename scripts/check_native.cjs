// Check native answer framing and no write retries against an in-memory pipe.
// No real desktop connection or Telegram request is made.
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { EventEmitter } = require('events');
const root = path.resolve(__dirname, '..');
let helper;
if (process.argv[2]) helper = fs.readFileSync(process.argv[2], 'utf8');
else {
  const bat = fs.readFileSync(fs.existsSync(path.join(root, 'assets/CodexChat.bat'))
    ? path.join(root, 'assets/CodexChat.bat') : path.join(root, 'CodexChat.bat'), 'utf8');
  helper = Buffer.from(bat.match(/__NODE_PAYLOAD_BEGIN\r?\n([\s\S]*?)\r?\n__NODE_PAYLOAD_END/)[1], 'base64').toString('utf8');
}
const seen = [];
class Socket extends EventEmitter {
  constructor() { super(); queueMicrotask(() => this.emit('connect')); }
  destroy() { this.emit('close'); }
  write(frame) {
    assert.equal(frame.readUInt32LE(0), frame.length - 4);
    const message = JSON.parse(frame.subarray(4).toString('utf8'));
    seen.push(message);
    let response;
    if (message.type === 'request') {
      assert.equal(message.hostId, undefined, 'Local follower must omit hostId');
      let result;
      if (message.method === 'initialize') {
        assert.equal(message.sourceClientId, 'initializing-client');
        result = {clientId: 'verified-client'};
      } else {
        assert.equal(message.sourceClientId, 'verified-client');
        if (message.method === 'thread-owner-discovery') result = {supportsUntrustedAppInput: true};
        else {
          assert.equal(message.targetClientId, 'desktop-owner');
          assert.equal(message.version, message.method === 'thread-follower-start-turn' ? 2 : 1);
          result = {result: {turn: {id: 'verified-turn'}}};
        }
      }
      response = {type: 'response', requestId: message.requestId, resultType: 'success',
        handledByClientId: 'desktop-owner', result};
    } else if (message.params.tool === 'list_threads') {
      response = {jsonrpc: '2.0', id: message.id, result: {success: true}};
    } else {
      queueMicrotask(() => this.emit('error', new Error('connection lost after dispatch')));
      return;
    }
    const body = Buffer.from(JSON.stringify(response));
    const header = Buffer.alloc(4); header.writeUInt32LE(body.length);
    // Deliberately fragment a frame to check buffering.
    queueMicrotask(() => { this.emit('data', header.subarray(0, 2)); this.emit('data', Buffer.concat([header.subarray(2), body])); });
  }
}
const context = vm.createContext({
  Buffer, setTimeout, clearTimeout,
  process: {env: {CODEX_APP_TOOLS_PIPE_PATH: 'fake-pipe', CODEX_TG_SESSION: 'session'}, stdin: {}, stdout: {write() {}}},
  require(name) {
    if (name === 'net') return {createConnection: () => new Socket()};
    if (name === 'readline') return {createInterface: () => ({on() {}})};
    return require(name);
  },
});
vm.runInContext(helper + '\nthis.answerTool=questionTool;this.appCall=callTool;', context);
(async () => {
  const prompt = '<send_user_message_question_reply>\n' + JSON.stringify([
    {questionItemId: '["request_user_input_async","call_original",0]', question: 'Question?', answer: 'Chosen option'},
  ]) + '\n</send_user_message_question_reply>';
  for (const active of [null, 'active-turn']) {
    const response = await context.answerTool('__question_reply', {threadId: 'session', prompt, activeTurnId: active});
    assert.equal(JSON.parse(response.result.contentItems[0].text).nativeTurnId, 'verified-turn');
    const native = seen.at(-1);
    const input = active ? native.params.input : native.params.turnStart.request.input;
    assert.equal(input.length, 1);
    assert.equal(input[0].text, prompt);
    assert.equal(input[0].type, 'text');
  }
  await assert.rejects(context.appCall('send_message_to_thread', {threadId: 'session', prompt: 'test'}), /connection lost/);
  assert.equal(seen.filter(x => x.params?.tool === 'send_message_to_thread').length, 1);
  console.log('PASS: native start/steer, original answer as one text input, owner routing, fragmented frames, no automatic write retry');
})().catch(error => { console.error(error); process.exitCode = 1; });
