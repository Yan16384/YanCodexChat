# Desktop async questions

Read this when maintaining the Telegram interaction path. These are observed internal interfaces, not a public stable API. Probe the installed version; preserve the target binding on incompatibility.

## Extract questions

The local Codex SQLite `threads.rollout_path` points to the JSONL rollout. Read it without editing Codex state. A `response_item` of type `function_call` named `request_user_input_async` retains `call_id` and JSON `arguments.questions`. The app-tools `read_thread` response can flatten this information into an agent message.

For question index `i`, the desktop identifies a question by compact JSON:

```js
JSON.stringify(["request_user_input_async", call_id, i])
```

The bundled implementation exposes the latest batch and subtracts native answers already present in the rollout. Cache by rollout path, file size and modification timestamp. A missing/unreadable rollout is a diagnostic, not proof that a question has been answered.

## Submit a native answer

The desktop UI recognizes a userMessage or accepted steeringUserMessage with exactly one text input containing:

```text
<send_user_message_question_reply>
[{"questionItemId":"[\"request_user_input_async\",\"call_example\",0]","question":"Original question","answer":"User-selected answer"}]
</send_user_message_question_reply>
```

Build this using JSON serialization. Do not concatenate unescaped user answers. Display readable answer text in history instead of exposing this wrapper.

Ordinary app-tools `send_message_to_thread` may create delegation/tool output. Use the native desktop follower path for the question answer so it reaches the original card as user input. Do not send a second fallback message after an uncertain submission.

## Native desktop IPC

The observed Windows endpoint is `\\.\pipe\codex-ipc`. Frame messages with four-byte little-endian length and UTF-8 JSON. This protocol is distinct from JSON-RPC app-tools.

1. Send `type=request`, UUID `requestId`, `sourceClientId=initializing-client`, `version=0`, `method=initialize`, `params={clientType:"yancodexchat"}`. Use the successful result's `clientId` thereafter.
2. Discover the owner with `thread-owner-discovery`, version 1, params `{hostId:"local", conversationId:target}`. Require the successful response's `handledByClientId` and compatible capability result.
3. Set `targetClientId` to that owner for the write. Omit envelope `hostId` for local follower methods; supplying it changes the expected version.
4. For an idle conversation use `thread-follower-start-turn`, version 2, params `{conversationId, turnStart:{request:{threadId, input, clientUserMessageId}, context:{inheritThreadSettings:true}}}`.
5. For an active turn use `thread-follower-steer-turn`, version 1, params `{conversationId,input,clientUserMessageId,restoreMessage}`. Include `restoreMessage={id:clientUserMessageId,text:wrapper,cwd:null,context:{}}`. The observed desktop implementation reads `restoreMessage.cwd` and `restoreMessage.context.workspaceRoots` before dispatch; omitting this object throws `Cannot read properties of undefined (reading 'cwd')`. A null cwd inherits the target desktop conversation; do not substitute the bridge directory or change the target workspace.
6. `input` is a one-element array `{type:"text",text:wrapper,text_elements:[]}`. Generate a UUID for `clientUserMessageId`. Preserve the original thread's model, permissions and settings.

Responses have `type=response`, matching `requestId`, `resultType=success|error`. Start/steer results are nested in `result.result`. Correlate the resulting turn ID; a steering answer may keep the active turn ID. Ignore unrelated broadcasts and reject client discovery requests with `canHandle:false`; the bridge must not claim ownership of any desktop conversation.

Use bounded connection/request timeouts. Retry discovery and reads as appropriate, but not a write whose delivery may have occurred. A version or owner error must surface clearly instead of silently creating an independent CLI conversation.

The missing-restoreMessage error was confirmed to occur before optimistic steering or backend dispatch. To recover an affected saved answer, first verify its original question ID is still pending and its answer is absent from the desktop history; only then explicitly requeue that user-selected answer. Do not apply this recovery to a timeout, disconnection or another unknown-outcome error.

## Callback state and concurrency

Use compact opaque callback keys under Telegram's 64-byte limit. Persist chat/user/session/original item ID/options/state. Check current ownership and the latest unanswered batch both at click time and before dispatch. Persist queued state before starting work; interrupted `sending` becomes `uncertain` on restart. A stale button or desktop answer never authorizes a new question answer.

Keep the polling loop available during Codex work. Dispatch async answers outside the normal per-session turn-completion queue, serialize the short read-and-dispatch section, and deduplicate final delivery by chat/session/turn ID. Suppress final pushes after leaving or switching the target session. `/stop` leaves the binding; it does not interrupt a desktop task already dispatched.

## Verification boundaries

- `check_interactions.py`: isolated synthetic rollouts and SQLite, fake Telegram; no network or desktop writes.
- `check_native.cjs`: in-memory framed pipe; checks original wrapper, owner/version routing, start/steer, fragmented frames and no write retries.
- Native discovery: live initialize, owner and model-setting reads establish interface availability only.
- Actual Telegram card delivery establishes rendering only. Confirm a human-selected answer appears as native input in the original desktop thread and dismisses the original card before reporting full round-trip completion. Never choose a personal option merely to validate delivery.
