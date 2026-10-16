---
name: yancodexchat
description: Build, repair, or package a Windows Telegram bridge that forwards messages to an already-running Codex desktop conversation and returns Codex replies to Telegram. Use this skill whenever a user asks for Telegram-to-Codex forwarding, phone access to a computer Codex chat, a Codex bridge bot, a one-click Windows BAT deployment, or a bridge that must keep the full conversation in the desktop Codex session. Enforce the single-BAT deployment contract below even when the user asks for a quick script or supplies an existing multi-file bridge.
compatibility: Windows 10/11 x64; a signed-in Codex desktop application exposing a compatible local interface; Telegram Bot API access. The BAT prepares portable Python and Node when missing.
---

# YanCodexChat

Build a local Windows service in which Telegram is only the phone-side input and output channel. The actual conversation must run in the already-open Codex desktop session, so messages sent from Telegram appear in that desktop thread and the desktop thread keeps the complete history.

## Reuse the implementation

When `assets/CodexChat.bat` is bundled with this skill, copy that BAT as the deployment template. In the source repository, use the root `CodexChat.bat`. Preserve its embedded payloads and two-variable configuration contract. Decode embedded code when inspection is required, then regenerate the payload after changing it. The bundled template includes the caller-thread bootstrap fix below. Real desktop list/read compatibility and full Telegram forwarding require separate verification on the target machine; a packaging simulation establishes neither.

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

### Async question interaction

When a desktop conversation uses `request_user_input_async`, show the actual question and all choices as Telegram inline buttons, plus free-text and skip actions. Add `/questions` and a menu entry to recover an existing question; `/cancel` cancels free-text entry. Only discover questions automatically during a Telegram-originated task or an explicit session/question read, preserving the desktop-only notification policy.

Read [references/interactions.md](references/interactions.md) when changing question extraction, native answer transport, callback state, or reply correlation. The desktop `read_thread` projection can flatten question cards into plain text. Keep the original tool-call ID and question index from the local rollout; the native answer must be a single text input containing the original `questionItemId`. Ordinary app-tools delegation of that wrapper does not establish that the desktop card was answered.

Keep Telegram polling responsive with background work. Async answers must bypass the queue waiting for the current turn to finish, so an active turn can accept steering input. Serialize dispatches, correlate resulting turns, and deduplicate final replies when both the original task and its answer observe the same turn.

Persist question ownership and state. Check the allowlist, chat, user, selected session, active binding, and current question again before dispatch. Expire questions answered on the desktop or superseded by a later batch. Accept one submission only; clear buttons after submission. Interrupted or timed-out writes have an uncertain result and must not replay automatically after retry or restart. Preserve histories and saved bindings.

This implementation handles async question cards. Blocking tool-input requests and command/file/permission approvals have different request IDs and protocols; add explicit support and validation before claiming those are covered. Internal native IPC compatibility must be checked on the installed desktop version.

Do not use Computer Use, screen clicking, remote window control, or `codex exec resume` for the normal existing-session path. Do not create or switch to a new Codex conversation automatically. If the desktop interface is unavailable, report that clearly and preserve the selected session binding.

The internal desktop app-tools transport is distinct from the public Codex App Server protocol. Public App Server documentation does not establish that desktop app-tools calls are supported. Verify the actual installed interface and response shape before claiming the bridge works; do not substitute a separate CLI conversation when the desktop interface is unavailable.

### Required startup context and error diagnosis

The observed desktop interface validates the caller's `threadId` even for the read-only `list_threads` probe. An empty ID returned JSON-RPC `-32602` with `Invalid app tool request` while the pipe was present and the desktop was running. The same pipe accepted the probe with an existing local thread ID. Apply these requirements to every generated bridge:

1. Resolve a nonempty caller thread before launching the Node helper. Use the requested existing session when present; otherwise read an existing, unarchived local thread ID from the current user's Codex state database in read-only mode. Inspect available columns before ordering by timestamps; handle schema differences explicitly. Discover the current user's paths instead of copying a developer's ID or directory.
2. Keep the bootstrap caller separate from the Telegram chat's selected target. Startup discovery must not create a conversation, change a saved binding, or require a third BAT variable such as `CODEX_TG_SESSION`.
3. If no existing conversation is available, say that startup needs an existing desktop conversation and ask the user to create one in the desktop app and retry. An unreadable database or unsupported schema needs its own diagnosis; neither proves the desktop is closed.
4. Retain the candidate pipe's JSON-RPC error code and sanitized message when diagnosing a failed probe. Distinguish no matching pipe, connection failure, invalid request parameters, and tool-call failure. A pipe returning `-32602` is reachable: inspect caller context and request shape before telling the user to restart Codex or reinstall runtimes.
5. Accept a probe only after a successful app-tools response with a parseable expected payload. A pipe existing or a Python process starting is insufficient. A successful `list_threads` probe establishes list access only; verify reads and message delivery separately.

### Prevent recurrence when packaging

- Exercise the default deployment with no inherited session variable: only the two user-editable BAT values may be required. A run that succeeds only after manually setting a session ID fails this deployment contract.
- Check that cold-start caller selection leaves saved Telegram bindings unchanged and handles the absence of any Codex conversation with the explicit diagnosis above. Use synthetic databases for schema variants when verification is authorized.
- Re-embed every modified source into the BAT before delivery. Decode the final BAT and compare its payload to the fixed source; editing `runtime/embedded/bridge.py` alone is temporary and the next launch can overwrite it.
- Bundle a sanitized copy of that same BAT in `assets/CodexChat.bat` when producing the `.skill`. Compare the packaged asset and public BAT bytes, and scan decoded payloads as well as visible text for credentials. A correct instruction document with an outdated bundled launcher is an incomplete update.
- Report packaging checks, real desktop list/read checks, and Telegram-to-Codex round-trip checks separately. Record unavailable checks as unverified; never promote a simulated health result into a real forwarding claim.

When verification is requested, run `python scripts/check_template.py` from the skill or source repository. The script reads the bundled BAT (or repository-root BAT), decodes and checks its public payloads, and exercises caller discovery with temporary synthetic databases and a mocked helper. It covers default startup without a session variable, saved-binding preservation, explicit targets, timestamp variants and no existing conversation. It performs no Telegram calls or desktop writes. A passing result does not establish real forwarding; perform live checks separately when authorized. Pass an explicit BAT path to inspect a different public template.

For interaction changes, also run `python scripts/check_interactions.py` and `node scripts/check_native.cjs`. These check synthetic question records, owner/session binding, six-choice rendering, text/cancel/skip, stale and repeated buttons, restart uncertainty, native start/steer envelopes, and no automatic write retry. They perform no desktop writes. Report live interface discovery, real button delivery, and a human-selected answer round trip separately.

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
- default startup supplies a valid caller thread automatically without adding a configuration variable or changing the selected target;
- an invalid app-tools request is diagnosed separately from an absent desktop pipe;
- the packaged `.skill` contains the corrected BAT payload and its public copy uses placeholders.
- async question choices, free-text and skip keep the original question ID; stale or repeated buttons cannot dispatch another answer;
- answers can be received while Codex works, and an uncertain write is not automatically retried;
- real question rendering and original-card answer completion are distinguished from offline checks.
