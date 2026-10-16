const net = require('net');
const { execFileSync } = require('child_process');
const { randomUUID } = require('crypto');
const readline = require('readline');

const configuredPipe = (process.env.CODEX_APP_TOOLS_PIPE_PATH || '').trim();
const targetThread = (process.env.CODEX_TG_SESSION || '').trim();
let cachedPipe = configuredPipe || null;
let sequence = 1;

function listPipes() {
  if (configuredPipe) return [configuredPipe];
  const command = "Get-ChildItem '\\\\.\\pipe\\' | Where-Object { $_.Name -like 'codex-*' } | ForEach-Object { $_.Name }";
  try {
    const output = execFileSync('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', command], {
      encoding: 'utf8', timeout: 8000, windowsHide: true,
    });
    return output.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  } catch (_) {
    return [];
  }
}

function request(pipeName, message, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const path = pipeName.startsWith('\\\\.\\pipe\\') ? pipeName : `\\\\.\\pipe\\${pipeName}`;
    const socket = net.createConnection(path);
    let buffer = Buffer.alloc(0);
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('Codex 桌面本地接口连接超时')), timeoutMs);
    socket.on('connect', () => {
      const body = Buffer.from(JSON.stringify(message), 'utf8');
      const header = Buffer.alloc(4);
      header.writeUInt32LE(body.length, 0);
      socket.write(Buffer.concat([header, body]));
    });
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 4) {
        const length = buffer.readUInt32LE(0);
        if (buffer.length < length + 4) return;
        const payload = buffer.subarray(4, length + 4);
        buffer = buffer.subarray(length + 4);
        try { finish(null, JSON.parse(payload.toString('utf8'))); }
        catch (error) { finish(error); }
        return;
      }
    });
    socket.on('error', error => finish(error));
  });
}

function envelope(tool, arguments_, callId) {
  return {
    jsonrpc: '2.0',
    id: sequence++,
    method: 'tools/call',
    params: {
      arguments: arguments_,
      callerSource: 'codex',
      callId,
      hostId: 'local',
      namespace: 'codex_app',
      threadId: targetThread,
      tool,
      turnId: callId,
    },
  };
}

async function discover() {
  if (cachedPipe) {
    try {
      const probe = await request(cachedPipe, envelope('list_threads', { limit: 1 }, randomUUID()));
      if (probe && probe.result && probe.result.success) return cachedPipe;
    } catch (_) { cachedPipe = null; }
  }
  for (const pipe of listPipes()) {
    try {
      const probe = await request(pipe, envelope('list_threads', { limit: 1 }, randomUUID()));
      if (probe && probe.result && probe.result.success) {
        cachedPipe = pipe;
        return pipe;
      }
    } catch (_) { /* another Codex pipe */ }
  }
  throw new Error('找不到 Codex 桌面本地接口。请确认 Codex 桌面端正在运行。');
}

async function callTool(tool, arguments_) {
  const callId = randomUUID();
  let pipe = await discover();
  let response;
  try {
    response = await request(pipe, envelope(tool, arguments_, callId), 10000);
  } catch (error) {
    cachedPipe = null;
    pipe = await discover();
    response = await request(pipe, envelope(tool, arguments_, callId), 10000);
  }
  if (response && response.error) throw new Error(response.error.message || JSON.stringify(response.error));
  if (!response || !response.result || response.result.success === false) {
    throw new Error('Codex 桌面本地接口调用失败');
  }
  return response;
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async line => {
  if (!line.trim()) return;
  let requestData;
  try { requestData = JSON.parse(line); }
  catch (error) { process.stdout.write(JSON.stringify({ error: `客户端输入无效：${error.message}` }) + '\n'); return; }
  try {
    const response = await callTool(requestData.tool, requestData.arguments || {});
    process.stdout.write(JSON.stringify({ ok: true, response }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: error.message }) + '\n');
  }
});
