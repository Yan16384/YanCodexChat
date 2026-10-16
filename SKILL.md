---
name: yancodexchat
description: Build, repair, or package a Windows Telegram bridge that forwards messages to an already-running Codex desktop conversation and returns Codex replies to Telegram. Use this skill whenever a user asks for Telegram-to-Codex forwarding, phone access to a computer Codex chat, a Codex bridge bot, a one-click Windows BAT deployment, or a bridge that must keep the full conversation in the desktop Codex session. Enforce the single-BAT deployment contract below even when the user asks for a quick script or supplies an existing multi-file bridge.
compatibility: Windows 10/11 x64; a signed-in Codex desktop application exposing a compatible local interface; Telegram Bot API access. The BAT prepares portable Python and Node when missing.
---

# YanCodexChat

Build a local Windows service in which Telegram is only the phone-side input and output channel. The actual conversation must run in the already-open Codex desktop session, so messages sent from Telegram appear in that desktop thread and the desktop thread keeps the complete history.

## Reuse the implementation

When `assets/CodexChat.bat` is bundled with this skill, copy that BAT as the deployment template. In the source repository, use the root `CodexChat.bat`. Preserve its embedded payloads and two-variable configuration contract. Decode embedded code when inspection is required, then regenerate the payload after changing it. The template's packaging and runtime preparation were checked with a simulated backend; real desktop-interface compatibility and Telegram forwarding still require verification on the target machine.

## Deployment contract

Treat this as the acceptance contract for every implementation:

1. Deliver a self-contained `CodexChat.bat`. Embed all required Python, Node and PowerShell code in the BAT; copying that one file into an empty folder must be sufficient. A launcher that depends on separately distributed source files does not satisfy this contract.
2. A new user copies only that BAT, opens it in a text editor, changes only the two values at the top, saves it, and double-clicks it.
3. The two editable values are:

   ```bat
   set "TELEGRAM_BOT_TOKEN=在这里填写Telegram机器人Token"
   set "ALLOWED_USER_ID=在这里填写Telegram用户ID"
   ```

   If multiple users are required, use the format the implementation actually parses, such as `ALLOWED_USER_IDS=id1,id2`, and document it in the BAT. Do not require a third user-edited value.
4. Do not require editing `.env`, TOML, YAML, JSON, INI, Python, Node, PowerShell, registry, scheduled-task settings, system environment variables, or fixed paths.
5. Automatically extract internal helper files into `runtime/embedded` on startup. Logs, databases, media and runtime files may be created locally; explain that single-file distribution does not mean zero runtime files. Preserve user records. Do not require downloading or copying separate helper scripts.
6. Resolve the project root from the BAT location (`%~dp0`). Support spaces, Chinese characters, other drive letters, and another Windows username. Never hardcode the developer's home directory, drive letter, Codex path, virtual-environment path, or temporary directory.

## What the BAT must do

On double-click, the BAT must:

- locate its own directory and use it as the project root;
- check Python, Node/npm when required, and the Codex desktop installation or local interface;
- reuse compatible installed runtimes; if missing, download portable runtimes from official sources into the project without manual commands or system PATH changes; install only dependencies actually used (the current standard-library implementation needs no pip, npm packages or Codex CLI);
- create `logs`, `data`, `runtime`, and `state` directories when the implementation needs them;
- inject the two BAT values into the current process without putting the token on a command line;
- call Telegram `getMe` to validate the token, validate that at least one allowlisted user ID exists, and check that the Codex desktop interface is reachable;
- use an exclusive lock and verify the service health before reporting success;
- prevent two polling processes from consuming the same bot updates;
- keep the service alive after the control window is closed;
- write detailed diagnostics to project-local logs without writing the full token;
- show a short result with the project directory, log path, authorized-user count, Codex check, and Telegram Bot check.

If the service is already running, print exactly that no second instance was started. If startup fails, show the failed stage, the reason, the log path, and a practical next step. Do not call a Python process existing by itself a successful health check.

The existing project may only support double-click startup. Preserve that verified behavior instead of inventing untested `start/stop/restart/status` parameters.

## Codex connection

Use the computer's already-running Codex desktop service. The existing implementation attempts to use an internal app-tools local Windows named pipe. This interface is version dependent and must be probed successfully on the target machine before being described as working:

- enumerate `\\.\pipe\codex-*` dynamically; never store a pipe UUID permanently;
- probe candidate pipes with the app-tools `tools/call` protocol and select the one that can call `list_threads`;
- frame each request as a four-byte little-endian payload length followed by UTF-8 JSON;
- use UUID-shaped `callId` and `turnId` values, plus the target thread ID, host ID, namespace, and tool name expected by the desktop service;
- cache the pipe only until it fails, then rediscover it;
- use `send_message_to_thread` to send a Telegram prompt and `read_thread` to observe the new turn and retrieve the final agent message;
- serialize requests per target chat and wait for an in-progress turn rather than mixing replies from different turns.

Do not use Computer Use, screen clicking, remote window control, or `codex exec resume` for the normal existing-session path. Do not create or switch to a new Codex conversation automatically. If the desktop interface is unavailable, report that clearly and preserve the selected session binding.

The internal desktop app-tools transport is distinct from the public Codex App Server protocol. Public App Server documentation does not establish that desktop app-tools calls are supported. Verify the actual installed interface and response shape before claiming the bridge works; do not substitute a separate CLI conversation when the desktop interface is unavailable.

## Telegram runtime behavior

- Poll updates from the Telegram Bot API with one process only.
- Reject every sender whose Telegram user ID is not in the allowlist.
- Bind each allowlisted Telegram chat ID to a Codex thread and persist that binding and local message records in SQLite or the project's existing state store.
- Keep menus for recent device chats, pinned chats, current chat, recent history, and help when the project already provides them.
- Show history as paired messages with blank lines, for example:

  ```text
  我：问题一

  Codex：回答一

  我：问题二

  Codex：回答二
  ```

- Preserve user messages and Codex final replies. Do not present bridge diagnostics, tool-progress events, or a failed local record as if it were a Codex answer.
- Handle ordinary text, Telegram photo messages, and photo captions. For a photo, download the largest Telegram photo variant to a project-local media directory, then send Codex a prompt containing the local path and caption and explicitly ask Codex to use its local image-reading capability. State clearly in the documentation when the desktop API accepts a path prompt instead of a native attachment.
- Handle download failures with a visible Telegram error and a log entry; do not silently discard a photo update.
- Return the final Codex answer to the same Telegram chat, splitting messages below Telegram's size limit.
- Keep the desktop Codex thread as the source of conversation history. The bridge's SQLite transcript is a delivery diagnostic and recovery aid.

## Credential handling

The user explicitly may place the Bot Token and allowlisted user ID in the BAT. Never print the complete token, put it in `SKILL.md`, README, logs, Git history, command arguments, or diagnostic screenshots. Health output may show only presence, length, or a short redacted form. Pass it through a child-process environment or standard input where possible. Tell users not to publish the BAT because it contains a credential.

## Implementation workflow

1. Inspect the existing project, its current BAT, state database, and working Codex integration before replacing code.
2. Preserve working session selection, whitelist, SQLite state, single-instance locking, and history formatting while replacing only the failing transport boundary.
3. Build the one-BAT startup path around the actual project root and automatically discovered dependencies.
4. Add explicit postconditions for token validation, Codex pipe discovery, selected-thread reads, message injection, final-reply extraction, and duplicate-instance prevention.
5. Run focused checks for syntax, BAT path handling with spaces and Chinese characters, photo-plus-caption parsing, token redaction, unauthorized-user rejection, and the real local Codex read/send path when a safe test conversation is available.
6. Report what was actually verified and identify any check that could not be performed. Never claim that a Telegram message entered the desktop thread without observing the corresponding new Codex turn.

## User-facing deployment instructions

Keep the instructions short and in this order:

1. Copy only `CodexChat.bat`.
2. Open the one BAT file.
3. Change `TELEGRAM_BOT_TOKEN`.
4. Change `ALLOWED_USER_ID` or `ALLOWED_USER_IDS`.
5. Save the file.
6. Double-click the BAT.
7. Confirm the health-check result and use the displayed log path if it fails.

Explain where the BAT may be placed, how to tell the service is running, how to stop it using the verified behavior, and how to start it again. Do not send the user into a multi-file configuration guide.

## Final acceptance checklist

Before delivery, verify or explicitly mark each item:

- one BAT is the only manual configuration and startup entry point;
- no extra installation command or environment-variable setup is required;
- paths work from another directory, drive, username, and Chinese or spaced project path;
- the token is absent from logs and documentation;
- unauthorized Telegram users are rejected;
- one polling instance is enforced;
- an existing Codex session can be listed, selected, read, and continued;
- ordinary text and photo-plus-caption messages reach the selected desktop thread;
- the corresponding Codex final reply returns to Telegram;
- a desktop-only Codex message is not sent to Telegram automatically;
- startup failures show an actionable diagnosis;
- the BAT reports actual Telegram and Codex health checks.
