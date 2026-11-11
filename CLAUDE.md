DO NOT SAY THAT THE USER IS CORRECT OR COMPLEMENT THEIR REQUEST. FORMAL, CONCISE COMMUNICATION SHOULD BE THE ONLY COMMENTARY PROVIDED.

# Development Requirements - REQUIRED
1. Server-side code is all in Python using `uv`, using commands like `uv run ...` or `uv add ...` or `uv run pytest ...` for executing, testing, linting, and managing dependencies.
2. **Frontend is Vue 3 + Pinia + Vite** (PRODUCTION): Frontend code is in `frontend/` directory. The `static/` directory has been sunset and should not be referenced for new development.
3. **Code Quality - Ruff Linting** (REQUIRED): All Python code must be linted with Ruff before committing. Run `uv run ruff check --fix src/` on changed files to auto-fix violations. New code must not introduce linting violations.

# High-Level Goal
We are building a web-based interface for Claude Agent SDK that provides:
1. **Single-Agent Mode**: Real-time streaming conversations with rich tool visualization
2. **Multi-Agent Mode (Legion)**: Teams of AI agents (minions) collaborating on complex tasks through structured communication

The SDK's streaming message responses are delivered via HTTP long-polling to a Vue 3 frontend which displays messages, tool executions, permissions, and multi-agent activity.

# Claude Agent SDK Integration - CRITICAL TECHNICAL KNOWLEDGE

## SDK Usage (REQUIRED)
```python
from claude_agent_sdk import query, ClaudeAgentOptions

# Basic streaming conversation
async def main():
    async for message in query(prompt="Create a Python web server"):
        print(message)

# With configuration
options = ClaudeAgentOptions(
    cwd="/path/to/project",
    permission_mode="acceptEdits",
    allowed_tools=["bash", "edit", "read"]
)
async for message in query(prompt="Build the project", options=options):
    process_message(message)
```

## SDK Configuration (CRITICAL)
```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd="/path/to/project",              # Project working directory (NOT working_directory)
    permission_mode="acceptEdits",       # Permission mode (NOT permissions)
    system_prompt={                      # System prompt configuration (preset or custom)
        "type": "preset",
        "preset": "claude_code"          # Use Claude Code preset
    },
    allowed_tools=["bash", "edit", "read"],  # Tool allowlist (NOT tools)
    setting_sources=["user", "project", "local"],  # Settings sources to load
    model="claude-3-5-sonnet-20241022"   # Model selection
)
```

## CRITICAL PARAMETER MAPPING
- Use `cwd` NOT `working_directory`
- Use `permission_mode` NOT `permissions`
- Use `allowed_tools` NOT `tools`
- Use `prompt=message` NOT positional argument in query()
- Always import from `claude_agent_sdk` NOT `claude_code_sdk`
- Use `ClaudeAgentOptions` NOT `ClaudeCodeOptions`

## Permission Mode Behavior (CRITICAL)
- `permission_mode="default"` means "prompt for everything NOT pre-approved"
- Pre-approved tools are defined in `.claude/settings.json` or `.claude/settings.local.json`
- Tools like WebFetch, Edit, Write, etc. require permission prompts unless explicitly pre-approved
- Only tools in the settings file's `permissions.allow` array bypass permission prompts
- Most tools should trigger permission callbacks in `default` mode - lack of prompts indicates SDK integration issues

# Development Process Requirements

## Testing and Verification Protocol
1. ALWAYS test actual SDK integration before claiming functionality works
2. NEVER assume parameter names or function signatures - verify with actual imports
3. Create minimal test files to verify integration, then DELETE them when done
4. Test each component in isolation before building complex architectures

## File Management Protocol
1. DELETE temporary test files (test_*.py, demo_*.py) after use
2. Do not leave debugging files in the project directory
3. Only keep files that are part of the core application

## SDK Integration Requirements
1. Use exact parameter names from CLAUDE.md specification
2. Test imports and function calls in isolation first
3. Handle JSON serialization of SDK objects properly
4. Always use try/except blocks around SDK calls

## Code Quality - Ruff Linting Workflow

**REQUIRED**: All Python code changes must be linted with Ruff before committing.

### Development Workflow
1. **Before Committing**: Run Ruff on the specific files you modified
   ```bash
   # Lint specific files you changed
   uv run ruff check --fix src/web_server.py backend/session_manager.py

   # Or use git to find changed files
   uv run ruff check --fix $(git diff --name-only --diff-filter=AM | grep '\.py$')
   ```

2. **View Violations** (without fixing):
   ```bash
   uv run ruff check src/module_name.py
   ```

### Zero Violation Policy

The codebase maintains **zero ruff violations**. `uv run ruff check src/` must pass with no errors.

**Requirements**:
1. **All code must pass `uv run ruff check src/` with zero violations**
2. **No `# noqa` comments without PR-level justification**
3. **Run `uv run ruff check --fix <file>` before committing** to auto-fix safe violations
4. **CI enforcement**: Any violation fails the check

**Rule sets enabled**: E (pycodestyle errors), W (warnings), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade), B (flake8-bugbear)

### Configuration
Ruff configuration is in `pyproject.toml`:
- Line length: 100 characters
- Target Python: 3.11+
- Unused imports in `__init__.py` are allowed

# Memory and Context Persistence - IMPORTANT

## Do NOT Use Built-in Auto-Memory for Session/Legion Workflows

Claude Code's built-in memory (`/memory` command, auto-memory in `~/.claude/`) is
**working-directory-specific**, not session-specific. This is unsuitable when:

- **Multiple minions share the same working directory** — memory cross-contaminates
- **Sessions persist across context resets** — working-directory memory doesn't travel with the session
- **Session archival/recovery** — built-in memory is not captured in session archives

### Disabling Auto-Memory

Sessions have a `disable_auto_memory` config flag (default: `False`). When enabled, the SDK
subprocess receives `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, preventing Claude Code from reading
or writing to its working-directory memory store.

Enable this for multi-agent (Legion) workflows or any session where isolated context is required.

### Recommended Alternatives

1. **Session History Distillation** (#691): Distilled history markdown for context continuity
2. **Task/Agent Messages**: Structured messages within the session
3. **Custom Session State**: Context in session `state.json` (persists with archives)
4. **Archive-Based Recovery**: Restore context from archived session data

### When Built-in Memory IS Acceptable

Built-in memory is fine for single-agent, single-session use where no archival,
multi-agent isolation, or cross-reset persistence is needed.

# Environment Configuration - Background Call Suppression

The Claude Agent SDK and Claude Code CLI honor a number of environment variables that suppress
ambient/background API calls. Claude WebUI applies these at session-launch time based on the
`background_calls` section of `~/.config/cc_webui/config.json`.

## Defaults (fleet-mode)

All flags default to ON (suppression enabled) except `dont_inherit_env` (off — breaks Docker/proxy flows).

| Config Field                  | Env Var                                  | Default | Effect                                       |
|-------------------------------|------------------------------------------|---------|----------------------------------------------|
| `disable_auto_memory`         | `CLAUDE_CODE_DISABLE_AUTO_MEMORY`        | true    | Suppress working-directory auto-memory       |
| `disable_claudeai_mcp_servers`| `ENABLE_CLAUDEAI_MCP_SERVERS=false`      | true    | Disable built-in Claude AI MCP polling       |
| `disable_background_tasks`    | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`   | true    | Suppress CLI background ambient operations   |
| `disable_nonessential_traffic`| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | true  | Block ambient telemetry/metrics              |
| `disable_cron`                | `CLAUDE_CODE_DISABLE_CRON`               | true    | Disable CLI's bundled cron (≠ our scheduler) |
| `disable_feedback_survey`     | `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`    | true    | Suppress survey prompts                      |
| `disable_telemetry`           | `CLAUDE_CODE_ENABLE_TELEMETRY=0`         | true    | Explicitly opt out of telemetry              |
| `subprocess_env_scrub`        | `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`       | false   | Strip credentials from spawned subprocesses (off by default — enabling this causes CLI to force permission_mode=default unless allowedTools declared explicitly) |
| `skip_version_check`          | `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK`    | true    | Skip CLI version check (latency)             |
| `dont_inherit_env`            | `CLAUDE_CODE_DONT_INHERIT_ENV`           | false   | Block env inheritance (breaks Docker/proxy)  |

## Per-Session Override

Session config and templates retain their existing override fields:
- `auto_memory_mode = "claude"` → re-enables auto-memory for that session
- `enable_claudeai_mcp_servers = True` → re-enables Claude AI MCP for that session
- `env_scrub_enabled = True` → forces scrubbing (additive with global default)

For other suppression flags, per-session `extra_env` has the highest priority
and can set or unset any env var.

## Task Tracking Tools Gating (issue #2010)

`task_tools_mode` on `SessionConfig` (`"default" | "on" | "off"`, default `"default"`) controls
`CLAUDE_CODE_ENABLE_TASKS` and `CLAUDE_CODE_ENABLE_TODO_TOOLS` together — two independent CLI
gates, ANDed by the CLI, that must both pass for TodoWrite/Task* tools to be available.
`CLAUDE_CODE_ENABLE_TASKS` is a tri-state kill switch (unset/`"true"` defers to the CLI's own
per-model-family allowlist; `"false"` forces tools off for every model).
`CLAUDE_CODE_ENABLE_TODO_TOOLS` auto-passes for a hardcoded allowlist of older/recognized Claude
model families and only consults the env var for models outside that list — which as of CLI
2.1.281 includes the entire Claude 5 family and any non-Claude model. `task_tools_mode="on"`/
`"off"` force both vars to `"true"`/`"false"` uniformly, overriding the CLI's per-model gating;
`"default"` omits both and defers entirely to the CLI. Same 3-tier Profile → Template → Session
cascade as `auto_memory_mode`, resolved in `backend/claude_sdk.py::_resolve_env_vars()`. See the
issue #2010 investigation notes for the CLI-binary-level trace this is based on.

## Auto Mode Classifier Model (issue #2285)

`auto_mode_classifier_model` (str|None, `permissions` area, Profile → Template → Session cascade)
maps to `CLAUDE_CODE_AUTO_MODE_MODEL`, emitted opt-in in `_resolve_env_vars()` (omitted when
unset; `extra_env` wins). It affects only the **client-side** classifier; a server-configured
classifier model (GrowthBook) takes priority inside the CLI. The value is a plain model ID or a
LiteLLM catalog alias `catalog--model`. `session_launcher._vet_classifier_model` drops a known
catalog alias (with a warning) on sessions not routed through LiteLLM, and for Docker sessions
registers it as a `passthrough_models` entry so the proxy addon does not rewrite it.

## Opt-in CLI Behavior Options (issue #2287)

Cascade-only fields (Profile → Template → Session, `features` area; no global/`BackgroundCallsConfig`
entry). `None`/`False` always emits nothing, so unset preserves current behavior. All are resolved
in `backend/claude_sdk.py` (`_resolve_env_vars()` before the `extra_env` merge, or `settings_payload`).
Stage A (verified against CLI 2.1.309 / SDK 0.2.173):

| Field | Mechanism | Notes |
|---|---|---|
| `disable_web_fetch` | env `CLAUDE_CODE_DISABLE_WEB_FETCH=1` when True | Disables WebFetch tool + web-fetch agent; overlaps (harmlessly) with `restricted_mode`/`disallowed_tools` |
| `disable_file_mentions` | env `CLAUDE_CODE_DISABLE_FILE_MENTIONS=1` when True | `@path` mentions are not expanded into file reads |
| `disable_idle_compaction` | settings key `idleCompaction: false` (via the `--settings` temp file) when True | **Kill switch only** — `true` never enables it; feature is server-gated. Honoring of the `--settings` layer by the CLI is unverified live |
| `idle_compact_min_tokens` (`ge=1`) | env `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` when set | CLI clamps to `max(100000, v)`, default 200000; inert unless idle compaction is enabled server-side |

Stage B — "Unattended runs" (same mechanism; every value emitted as `str(int)` only when not None,
via `_RETRY_INT_ENV_MAP` in `backend/claude_sdk.py`):

| Field | Env var | Bounds (ms) |
|---|---|---|
| `retry_watchdog` (bool) | `CLAUDE_CODE_RETRY_WATCHDOG=1` when True | master switch — the three watchdog tunables are only read by the CLI in this mode (UI `showWhen`) |
| `retry_watchdog_max_wait_ms` | `CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS` | `>=1`, default unbounded |
| `retry_watchdog_max_usage_limit_wait_ms` | `CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS` | `>=1`, default unbounded |
| `retry_watchdog_usage_limit_recheck_ms` | `CLAUDE_CODE_RETRY_WATCHDOG_USAGE_LIMIT_RECHECK_MS` | 60000–10800000 |
| `overloaded_retry_base_delay_ms` | `CLAUDE_CODE_OVERLOADED_RETRY_BASE_DELAY_MS` | 500–32000 (independent of the watchdog) |
| `overloaded_retry_max_delay_ms` | `CLAUDE_CODE_OVERLOADED_RETRY_MAX_DELAY_MS` | 32000–64000 (independent of the watchdog) |

Enabling the watchdog with unbounded waits can stall a session for hours; set the max-wait values for
unattended runs.

Pydantic bounds mirror the CLI's accepted ranges; re-audit them when upgrading the SDK. Cascade-style
opt-in vars like these do not belong in `_BACKGROUND_CALL_ENV_MAP`.

## Auto-Compact Window for LiteLLM Sessions (issue #2288)

On LiteLLM-routed sessions the CLI assumes a 1M window for unknown `catalog--model` aliases, so
auto-compaction can fire too late for 128k–200k models. The launcher
(`session_launcher._resolve_auto_compact_window`) derives `CLAUDE_CODE_AUTO_COMPACT_WINDOW` per
model: catalog model `context_window` (optional, tokens, >= 1000) first, else LiteLLM's
`get_model_info(...)["max_input_tokens"]` (unmapped local/custom models yield nothing). Tier
sessions use the **minimum** known value across the three tier models (one CLI window per
session); non-Docker tier fallback uses the default tier only. Emission rule on that minimum `v`:
`100000 <= v < 1000000` emits the integer; `v < 100000` emits nothing and logs a warning (the CLI
clamps UP to 100k, which would overflow the model — never clamp up); `v >= 1000000` or unknown
emits nothing (set `context_window` >= 1000000 to opt a model out of a LiteLLM-reported cap). The
env var only lowers the CLI window. User `extra_env` always wins; non-LiteLLM sessions are
untouched. Docker sessions receive it via `CLAUDE_DOCKER_EXTRA_ENV`. Manual workaround:
`extra_env CLAUDE_CODE_AUTO_COMPACT_WINDOW=200k` or `/autocompact 200k`.

## Live Provider Model Switch (issue #2333)

`POST /api/sessions/{id}/provider-model` (`backend/provider_model_switch.py`) switches a LiteLLM-catalog
session's model without a restart, mirroring #1673 but never touching `config["model"]` (so it does not reuse
`SessionManager.update_model`). Non-Docker: `sdk.set_model("{catalog}--{model}")` — the single proxy holds every
entry's credentials, so a cross-catalog switch needs no key/route re-issue. Docker: the backend swaps the session's
`LiteLLMProxyManager` routing entry (`update_session_routing_model`, versioned, other fields preserved) and the
sidecar addon re-reads `/routing` (<= once per second, 1 s timeout, keeps last good) before each model rewrite.
Apply happens first, persistence after; a failure leaves config untouched. A session's launch-bound settings
(provider<->direct / tier routing, Docker, classifier model, auto-compact window drift, alias missing from the
running router) are diffed against a launch snapshot (`backend/restart_required.py`) into the runtime
`restart_required` reasons list in the session payload (not persisted; cleared on (re)start); the Settings UI shows
an amber banner + Model Tuning chip that opens the existing manage-session modal's Restart action.

## Loaded-History Memory Budget (issue #2030, stage 4 PR 4d)

The browser keeps a bounded amount of a long session's history, measured in bytes of the history file the
loaded records cover (`frontend/src/utils/historyBudget.js`): **64 MiB desktop, 32 MiB on a phone profile**
(viewport < 768 px, `navigator.deviceMemory <= 4`, or a coarse pointer); hidden support override
`localStorage.historyBudgetMiB`. Older history loads only on demand (scroll or jump; no background loading), an idle trim releases the old end back to
0.75 x budget (never within 12 viewports of the viewport, at most every 30 s), a session that is no longer
current keeps `min(8 MiB, budget/4)`, and the pinned island releases its far edge past the budget. Pages the user
asks for are never refused by the budget (a user scrolling up keeps loading). Details: `frontend/CLAUDE.md`.

## Relay (issue #2103, outbound stage 1a)

A **relay** is a Library asset for an external messaging workspace (Slack today): `RelayRecord` in
`data/relays/{slug}.json`, managed by `backend/relay_manager.py` (`RelayManager`). One relay per Backend.
Credentials are **references to existing vault secrets**: only secret **names** are stored
(`user_token_secret`, `app_token_secret`; the owner creates them in Library -> Secrets). Values live in the
credential vault, are read in-process via `RelayManager.get_token()` only, are never returned by any API,
logged, or put in exception text, and never enter containers. `PUT /api/relays/{id}/secrets
{user_token_secret?, app_token_secret?}` (either alone) checks that the secret exists and that its value has the `xoxp-` / `xapp-` prefix, and verifies the user token with
`auth.test`; there is no token-paste route. A secret used elsewhere (sessions, templates, profiles, MCP servers) is selectable like any other: sharing a credential is
not special-cased. The Secrets library is the one place that shows usage: its usage chip tooltip lists relay credential
references (`usage.relays`, e.g. "Relay: Slack"; `compute_secret_usage(..., relays=...)`). Only the `relay-` name prefix (secrets the OAuth flow itself creates) stays unassignable
(`validate_assigned_secrets`, `SecretsVault.resolve_secrets_for_assignment`). Deleting a relay deletes only secrets it created (`relay-{id}-*`), never user-owned ones. If a referenced
secret is deleted, `has_*_token` turns false and `*_secret_missing` true (tools fail with `not_authed`, no crash). OAuth
(`/oauth/initiate`, user-scope authorize URL without PKCE; the resulting token is stored as a normal relay-prefixed vault secret and referenced by name; exchanged via `oauth.v2.access` through the
`OAuthCallbackListenerManager` relay hook) works only with a localhost or https redirect URL.

- **Adapter seam** (`backend/relay/`): `RelayAdapter` protocol (`adapter.py`), `SlackAdapter` on httpx
  (`slack_adapter.py`; bounded retry on rate limits, tokens fetched per call), `RelayEvent` / `is_agent_message`
  (`events.py`), `SubscriptionStore` protocol, `RelayHandleIndex` (`handles.py`). `start_events()` stays unused:
  inbound runs through `relay/inbound/` (see below).
- **Attachment cascade**: `relay_id`, `relay_footer_enabled`, `relay_footer_template`, `relay_channel_policies`,
  `relay_handle` (new profile area `relay`; `relay_handle` is session-tier only).
- **Adapter registry** (`relay/adapters.py`, issues #2103/#2346): one `AdapterSpec` per adapter type
  (`server_name` `relay_slack`, tool -> policy-action map, write tools, `policy_model`); `parse_relay_tool()` /
  `is_relay_tool()` recognise `mcp__relay_{adapter}__*` of a registered adapter. The gate, permission bridge,
  launcher and MCP factory read the spec; nothing outside `slack_policy.py` / the Slack spec knows Slack's values.
- **Policy model is adapter-owned.** `relay/policy.py` holds only the adapter-neutral parts (`Decision`, kinds,
  `EffectivePolicy`, per-key `merge_policies`, `PolicyModel` protocol, `normalize_policies`). Slack's
  (`blocked | read_only | ask | auto`; unlisted = unset; targets are channel IDs `^[CGDW][A-Z0-9]{6,}$`) lives in
  `relay/slack_policy.py` (`SlackPolicyModel`, `decide()` matrix). `GET /api/relays/{id}/policy-model` returns
  `describe()` (`values`, `target_pattern`, `actions`) for editors.
- **Policy config is keyed by relay**: `relay_channel_policies: {relay_id: {target: policy}}` at profile, template and
  session tier. Effective policies are merged **per target within each relay** across profile < template < session and
  read **live per call** (`RelayService.effective_policies` / `effective_relay_policies`), not from the start-time
  `SessionConfig`, so runtime edits need no restart. Writes accept the nested shape only (flat input and unknown
  relay ids -> 422/400, targets/values validated by that relay's adapter model); a stored legacy flat dict is
  normalised **at read time** (wrapped under the sole relay, else dropped with a warning) and upgraded on the first
  runtime edit. Dangling relay ids are ignored on read.
- **Policy REST** is relay-qualified: `PUT|DELETE /api/sessions/{id}/relays/{relay_id}/policies/{target}` (404 session or
  relay, 409 relay not the session's attached relay, 422 adapter validation). The adapter-less
  `/api/sessions/{id}/relay/policies/{channel}` route was removed (no alias).
- **Handle**: `relay_handle` is assigned once on first attachment (`slugify_name(name)`, numeric suffix on
  collision), is unique Backend-wide, editable (409 on duplicate), and **never re-derived on rename** (it is
  independent of `session.slug`). A whole-config PATCH that omits `relay_handle` / `relay_channel_policies`
  keeps the existing values.
- **Trigger rule**: `!handle` is the inbound instruction syntax, so outbound text must not contain it.
  `relay/safety.py::contains_trigger_syntax()` (regex `(?<![\w!])!({HANDLE_PATTERN})(?![\w-])`, one shared
  `HANDLE_PATTERN`, case-insensitive: `!Deploy` is refused too; handles stay lowercase) runs on the raw,
  HTML-unescaped and NFKC forms; code spans/fences are not exempt and Slack
  `<!here>` is refused. Footer templates are validated with it (<= 500 chars; variables `{session_name}`,
  `{session_handle}`, `{session_link}`).

### Outbound tools, permission integration, audit (outbound stage 1b)

- **MCP server `relay_slack`** (`backend/mcp/relay_mcp_tools.py`; tools `mcp__relay_slack__*`; **hard break**: the former
  `mcp__relay__*` names have no alias, saved allow rules for them stop matching and sessions started before the upgrade
  must be restarted): `post`, `reply`, `read_thread`, `search`,
  `upload_file`, `react`, `mark_read` (no edit/delete; 1c-ii adds read-only `get_identity` and `find_channels`). Attached by `session_launcher` only when the
  effective config has `relay_id` and the relay has a user token. `mcp__relay_slack` is deliberately **not** in the
  auto-allow list, so every call reaches the permission callback. `channel` accepts an ID, `#name`, `@name`, or a user ID
  (`U…`, DM via `conversations.open`) and is resolved to a canonical ID before any policy check (see 1c-ii below).
- **Two gates, one decision** (`relay/gate.py`): the permission pre-hook
  (`relay/permission_bridge.py`, top of `permission_callback` for `mcp__relay_slack__*`) allows reads and `auto` sends
  without UI, denies `blocked` / `read_only`-send and `!handle` text, and prompts for `ask` / unlisted channels
  (`tool_call.permission.relay_prompt`, kinds `ask_send` / `choose_policy`, UI `RelayPermissionPrompt.vue`).
  The **handler re-checks live policy and is authoritative** and consults the permission mode only to refuse in don't-ask
  (below). `auto` sends and reads on listed channels need no prompt in any mode; `blocked` / `read_only` refuse in any mode. A send that needs approval
  (`ask` / unlisted) proceeds on a one-shot grant (`relay/grants.py`; bound to session + tool + canonical input hash,
  60 s, single use) that the callback's approved prompt left (default / acceptEdits: audit `prompt_via=callback`, one
  prompt in total), otherwise the **handler prompts the owner itself** (issue #2353; `relay/prompt_broker.py`,
  `RelayPromptBroker`, over `PermissionService.request_decision`). That covers auto, `bypassPermissions`,
  pre-approved allow rules and classifier-approved calls, where the callback never runs: bypass never widens what the
  agent may post, only the channel's `auto` policy sends unprompted. The wait is bounded by
  `RELAY_PROMPT_TIMEOUT_SECONDS = 600` (`relay/constants.py`). After an allow the handler **re-evaluates policy and the
  relay attachment fresh** (a policy edited to `blocked` / `read_only`, a detached relay or a removed credential while
  waiting refuses), and sends. Broker approvals mint no grant; the audit row has `prompt_via=handler`; `approval` values
  are unchanged (`auto`, `prompt_allow`, `prompt_promote`). If the auto-mode classifier denies the call, the handler
  never runs and no prompt appears (intended).
- **Unattended = fail closed, immediately.** `PermissionService.can_prompt` is false when the launched SDK denies
  unattended prompts (`deny_unattended_permission_prompts`), the session `is_ephemeral`, it has no permission callback
  (scheduler launches pass none) or it is not running. A permanent-minion schedule only enqueues into an ordinary attended
  session, so it prompts. Unattended `ask` -> `approval_unavailable`; unattended unlisted -> `policy_unset`; nothing waits.
- **Don't-ask mode fails closed (issue #2360).** In `dontAsk` (`PermissionMode.DONT_ASK`; "do not wait for me") the
  handler refuses instead of prompting. `can_prompt` returns `(False, "dont_ask_mode")` for the session's live
  `current_permission_mode == dontAsk`, checked only AFTER the unattended reasons (an unattended don't-ask session keeps
  `approval_unavailable` / `policy_unset`). `_ask_owner` maps it to `approval_unavailable_dont_ask` (`ask` channel) /
  `policy_unset_dont_ask` (no policy) through the normal refusal path: audited as denied, same message + JSON block
  formatter, the broker is never called, nothing waits, no policy is saved, nothing is sent. Message: the session is in
  don't-ask mode; the owner can set the channel to `auto` or change the mode. This applies to post, reply, upload_file,
  react (ask and unlisted) and, **owner-visible interpretation of "reads work as today"**, to read_thread / mark_read on
  an UNLISTED channel (they would raise a choose-policy prompt, so they are refused with `policy_unset_dont_ask`);
  reads of listed channels, search, get_identity and find_channels are unchanged. **Bypass keeps prompting** for `ask`
  and unlisted channels (deliberate: failing open on external writes is too dangerous); default, manual, acceptEdits,
  plan and auto also prompt. The mode is read per call (no restart; switching out resumes prompting). A prompt already
  waiting when the session switches into don't-ask stays answerable (`re_authorize` does not re-check the mode); a valid
  one-shot grant is honoured; only calls that START in don't-ask refuse. Subagents share the session mode. The CLI itself
  denies non-allowed tools in don't-ask before the relay runs (no change). Tool descriptions are static per launch:
  sessions started before this change must be restarted to show the new text.
- **Interrupt / stop / restart / reset / task cancellation** while a handler prompt waits: the prompt is cleared
  (`deny_for_session`, `cleanup_pending_for_session`, a new SDK epoch, or `CancelledError`), the call ends refused
  (`approval_cancelled`), the card is marked denied and nothing is sent.
- **Tool card linkage (AC8)**, in degrading order: (1) a record-only `PreToolUse` SDK hook
  (`relay/call_registry.py`, `RelayCallRegistry`, registered by `session_launcher` only where the relay MCP server is
  attached; matcher derived from the adapter specs, e.g. `mcp__relay_slack__.*`, so no other tool pays a round trip; it
  returns `{}`, never allows/asks/denies and never raises) gives the handler its `tool_use_id`/`agent_id`
  (FIFO per `(session, tool, input digest)`, 5 min expiry, claimed — and so discarded — by every gated handler call whether or not it prompts, cleared on terminate/new SDK epoch), so the prompt appears on
  the call's own card, which shows "Waiting for your approval"; (2) no registry entry (hook not delivered, e.g.
  `bare_mode` may skip hooks; unverified) -> scan the session's tracked tool calls for the same tool + input; (3) no
  card -> a standalone `relay-prompt-{request_id}` card carries the prompt. In case 3 the owner still sees and answers the
  prompt, but the original tool card only shows "running" and is not linked to it (degraded AC8; the synthetic card is
  finalised on every outcome). Hook failures never block a tool call.
- **One awaiting record per tool call (issue #2355)**: the #1694 ordering barrier is keyed by TURN id and a turn is several SDK
  assistant messages, so after a text-only message of the turn the permission request can reach the tracker before the
  tool_use message that carries the input. `request_decision` therefore sets the callback's real input on the tracked call first
  (`update_tool_call_input(..., store=False)`, no separate record) so the single awaiting_permission record has it, and
  `update_tool_call_input` is a no-op (no store; `_emit_tool_call_updates` skips the re-emit) when the call is no longer PENDING
  and the input is unchanged. A different input still stores; the #2109 `{}` -> real input fill of a PENDING placeholder is intact.
  Applies to every permission-gated tool, not only relay.
- **Prompt answers** (`relay_choice` on the permission REST call): `Allow and set channel to auto` persists
  `auto` at SESSION tier live (no restart); `choose_policy` persists the chosen policy, and a policy that forbids
  the action (e.g. `read_only` for a send) denies it. Deny persists nothing. Relay prompts never offer
  permission-rule suggestions.
- **Refusal parity (issue #2355)**: the permission pre-hook deny (default / acceptEdits, where the callback runs first) carries the
  same content as the handler's refusal (auto): human message + `\n` + the structured JSON block, built by one shared
  formatter (`relay/gate.py::refusal_text` / `refusal_detail`, also used by `error_result`). `blocked` stays "not accessible to
  this session" with no name or label. Pre-hook audit rows are unchanged.
- **Refusals** are `is_error` results with a human block plus a JSON block
  `{"error":"relay_policy","code":...,"channel","action","policy"}`; codes: `channel_blocked`,
  `send_not_permitted`, `approval_denied`, `approval_timeout`, `approval_unavailable`, `approval_cancelled`,
  `approval_unavailable_dont_ask` / `policy_unset_dont_ask` (don't-ask; `ask` / unlisted),
  `policy_unset` (unattended + unlisted only), `trigger_syntax_refused`, `channel_unresolved`, `no_readable_channels`.
  (`approval_required` is retired: the handler prompts instead.) Blocked channels only say "not accessible to this session".
- **Search** (`relay/search.py`) is scoped with `in:` filters to read_only/ask/auto channels (chunks of 10;
  Slack treats several `in:` as OR), then post-filtered from result metadata against the same set. Unlisted
  channels are excluded (a note gives the count) rather than prompting. **Untrusted wrapper**: `search` and
  `read_thread` results are returned inside a **nonce-bearing fence** (`relay/safety.py::wrap_untrusted`):
  `[UNTRUSTED EXTERNAL CONTENT id=<nonce> from Slack — …it ends only at the marker bearing this same id…]` …
  `[END UNTRUSTED EXTERNAL CONTENT id=<nonce>]`. The payload is serialized first, then a fresh
  `secrets.token_hex(8)` is generated (regenerated if it occurs in the payload), so content cannot close or forge
  the fence whatever Unicode tricks it uses; marker-prefix neutralisation (`[END UNTRUSTED` ->
  `[END_UNTRUSTED`) stays as defense in depth only. Empty results are wrapped too. `wrap_instruction()` is the
  sibling (`[OWNER INSTRUCTION id=…]`) for the inbound stage's self-DM instructions (unused in 1b).
  Everything Slack-user-controlled returned to the agent (message text, `user_name`, `channel_name`, permalink)
  lives inside the fence; outside it only IDs, timestamps, our own messages, sanitized provider error codes
  (`safe_error_code`) and plain opaque cursors are returned. Policy errors name channels by ID only.
- **Names** (`relay/names.py::NameResolver`): user IDs via `users.info`, channel IDs via `conversations.info`,
  cached in memory for the process lifetime (LRU 2000, no persistence), resolved lazily with a 3 s timeout and a
  60 s failure back-off, falling back to the ID, never blocking a prompt. Names are cleaned (control/zero-width/bidi
  characters stripped, 80 chars). They appear in the permission prompt (`channel_label`: `#name`, `DM with <name>`,
  `your self-DM` from the relay's `owner_user_id`; plain text), in audit records (`channel_name`) and inside the
  wrapper.
- **Search flags self-DM agent posts (issue #2355)**: Slack search results carry no app/bot identity or metadata, so
  `search` sets `is_agent_message` (in addition to `is_agent_message()` from `events.py`) only when the result's channel is
  the owner's self-DM, its `user` is the relay's `owner_user_id` AND `has_agent_marker(text)` (the send path's matcher, on the
  untruncated text). Unknown self-DM or owner -> never flagged (fail closed); the marker elsewhere or from anyone else never
  flags. `read_thread` is unchanged. Edge case: the owner hand-typing the marker flags their own message.
- **Outbound tagging**: every `post` / `reply` carries Slack metadata `event_type=cc_webui_agent_message` with
  `session_id`, `handle`, `relay_id`; a footer (default on, `relay_footer_enabled`) is a `context` block after the
  body `section` blocks (`{session_link}` renders empty: the Backend does not know a public URL). Footers show the
  handle as plain text and sanitise `!word` out of the session name (`strip_trigger_syntax`). Uploads carry the
  footer in the file comment and **no metadata** (Slack's external upload API cannot attach any).
- **Audit** (`relay/audit.py`): `data/relays/{relay_id}/audit.jsonl`, 10 MB x 5 rotation, whitelisted fields only
  (text is a sha256 + 200-char preview), covers post/reply/upload/react/mark_read outcomes, denied attempts and
  policy changes; `GET /api/relays/{id}/audit`.
  **Policy changes (issue #2355)** all go through one audited write path: `SessionManager.update_relay_policy(...,
  *, source)` (`source` is REQUIRED, no default: `editor` | `prompt`) and `update_session` (`_replace_config`, flat
  CONFIG_FIELDS, create-time overrides: `source=config`) diff `relay_channel_policies` (normalised) **inside the
  per-session lock** and pass the per-target diff to sync policy-change callbacks; `RelayService` registers one that
  only schedules the audit write (FIFO task; failures are logged and swallowed, never block or fail the edit).
  One record per changed `(relay_id, target)`: `event=policy_change`, `channel_id`, best-effort `channel_name`,
  `old_policy` (null = none), `policy` (new; null = removed), `source`, `tier=session`, `session_id`, `handle`,
  `session_name`; a no-op write records nothing. Create-time overrides are `source=config` with `old_policy=null`.
  Prompt-driven records keep their pre-#2355 fields (`tool`, `approval=prompt_promote`, `channel_name` = label) and
  only gain `source=prompt` (no `old_policy` / `tier`; the viewer then shows `→ new`). Profile/template edits
  (`ApplicationService.update_template/update_profile`) are audited **once per changed target per tier edit**
  with `source=tier=template|profile`, `tier_id`, `tier_name`, `session_id=null`; there is **deliberately no
  per-session fan-out** of "effective policy changed" records for inherited edits (it would cost a before/after
  merge per dependent session and N near-duplicate records), so the viewer's session filter does not show tier
  records and the relay-wide view does. The audit viewer renders `old → new` (`(none)` for null) plus a quiet
  source/tier tag.

### Self-DM marker, identity and discovery (outbound stage 1c-ii)

- **Marker**: the relay (never the agent) prefixes every `post` / `reply` / `upload_file` comment sent into the owner's
  self-DM with `SELF_DM_MARKER` = `:robot_face: [cc-agent]` (`relay/safety.py`), in `text` AND in the first visible section
  block (Slack renders blocks, not `text`, when both exist; the marker counts against the 3000-char section and 35000-char
  body limits). It is keyed on the **resolved channel ID** (D-id, owner `U…` and `@<owner name>` all end in the same
  `D…`), never agent-controlled; text already starting with a marker is not double-prefixed. `has_agent_marker()` accepts
  the shortcode and U+1F916 (+ optional U+FE0F) followed by `[cc-agent]`, NFKC / zero-width / bidi tolerant, case-insensitive.
  **Inbound rule (stage 3): an instruction is a self-DM message from the owner AND with no `app_id`/`bot_id` AND without the
  marker.** A human typing the marker only demotes their own message to untrusted content (fails safe).
- **Self-DM identification** (`relay/selfdm.py`): in-memory cache (keyed by owner) -> `RelayRecord.self_dm_channel_id`
  (persisted, not secret, in the public record; set on credential save / Test connection / OAuth, cleared when the owner
  changes) -> `open_dm(owner)` (then persisted). A send whose resolved target is a `D…` and whose self-DM cannot be
  determined (no owner, `open_dm` failure, nothing persisted) **fails closed**: `self_dm_unresolved`, audited as denied,
  nothing sent. `C…`/`G…` targets are never affected.
- **`get_identity`** (no policy, no prompt): `{owner_user_id, self_dm_channel_id, workspace_id}` outside the fence (plus a
  note when the self-DM is unknown), `display_name` / `workspace_name` inside the nonce fence.
- **`find_channels`** (`relay/directory.py`, `RelayDirectory`; reused by stage 2b): `query?`, `kinds?` (`public`, `private`,
  `dm`, `group_dm`, `person`; default without `person`), `limit` (<= 50, default 20), opaque offset `cursor`. Case/diacritic-
  insensitive substring on **names only** (no topics). Each entry `{id, kind, name, policy}` (`policy` null = unlisted;
  **blocked ones are hidden** and not counted; a `person`'s policy is its DM's, null when the DM is unknown); the whole entry
  list is inside the nonce fence, the counts / `next_cursor` outside. `person` requires a query of >= 2 characters
  (`query_required`). Read-only metadata: it never grants a send right (the gate and grants still apply to a later send).
  The directory caches a conversations snapshot (`conversations.list` public/private incl. channels the owner has not
  joined, `users.conversations` for DMs, TTL 5 min) and a people snapshot (`users.list`, TTL 10 min), each bounded to 5000
  entries, refreshed single-flight, kept stale on error, with a 60 s failure back-off and forced refreshes (a name that is
  missing) at most every 10 s.
- **`@name` / `#name`** (`RelayPolicyGate.resolve_channel`): exact match, case/diacritic-insensitive (NFKC casefold), on
  display name, real name or handle (people; deleted excluded) or channel name; never prefix-guessed. One match -> ID
  (`open_dm` for a person), several -> `name_ambiguous` (candidates **inside the fence** as a third result block, none in the
  refusal text), none -> `name_not_found` after one forced refresh; if `users.list` is not permitted the error says to use
  IDs. Resolution happens before any policy check; the permission pre-hook lets name-lookup failures through to the handler
  (nothing is sent) so the structured refusal with the fenced candidates reaches the agent.
- **Audit**: `get_identity` / `find_channels` are recorded minimally (`query_sha256`, 40-char `query_preview`, `kinds`,
  `result_count`; no contents, no channel names). `self_dm_unresolved` denials go through the normal denied path.

### Settings UI and Slack app manifest (UI stage 2a)

- **UI**: Settings -> Library -> Relays (`/settings/relays`, `LibraryRelaysSection.vue`) creates the one Slack relay,
  shows `has_user_token` / `has_app_token` chips (never values; "secret missing" when a referenced secret was deleted), runs
  **Test connection** and holds the credentials form (two pickers listing vault secrets by NAME, a "Create a secret" link to Library -> Secrets; no token is ever typed here) plus the collapsible **Slack app setup** guide (steps, manifest with Copy /
  Download, scope list with reasons). OAuth sits under "Advanced" and renders only on https or localhost. The **Relay**
  area (`RelaySection.vue`, profile area `relay`) is available in profile, template and session editors: relay select,
  footer on/off + text, session-only handle (saved by the form's Save through its own endpoint, first in the sequence), channel policy editor (UI stage 2b, below).
- **Manifest**: `GET /api/relays/{id}/manifest` is generated from `backend/relay/slack_scopes.py` (`USER_SCOPES_*`,
  `USER_EVENTS_INBOUND`, `METHOD_SCOPES`). `test_relay_manifest.py` fails when `slack_adapter.py` calls a Slack method
  with no scope entry or a scope is unused; OAuth requests the same scope list. The app is "ccWebUI Relay", acts with the
  owner's own user token, has **no bot user and no bot scopes**; inbound events + Socket Mode are included by default
  (toggle) so the owner does not reinstall later. The app-level token (`connections:write`) is created in the Slack UI.
- **Restart semantics**: attaching a relay (`relay_id` unset -> set) to a running session that launched without one adds the
  `relay_not_attached` reason to `restart_required` (existing amber banner / restart chip), because the MCP server is attached
  only at launch. Footer, handle, policies and switching or clearing `relay_id` are read live per call: no restart.

### Policy editor, session surface and audit viewer (UI stage 2b)

- **Policy editor** at every tier (`RelayPolicyEditor.vue`): live at session tier through the relay-qualified policy REST (no restart), own-tier-only nested emit into the whole-config form at profile/template/schedule tier. Inherited profile/template entries are shown with a tier marker and overridden per target; the UI never sends inherited keys (template/profile policies are inherited live, not copied; `_compute_initial_session_overrides` would otherwise freeze them). Rendered from `policy_model` (incl. adapter-owned `value_help`).
- **Picker route** `GET /api/relays/{id}/conversations` (`backend/routers/relay.py`): wraps `RelayDirectory.search` through a session-less `RelayPolicyGate.relay_context()` (`DirectoryContext` protocol) with **no policies applied** (blocked and unlisted entries both appear; the agent's `find_channels` still hides blocked); `?ids=` returns `{labels}` through `NameResolver.channel_label`. Slack is called only on snapshot refresh, never per keystroke. There is no route to open a DM: a person without a known DM is shown disabled, the first agent message opens it and prompts for a policy.
- **`SessionInfo.relay_summary`** (runtime-only, never persisted, like `restart_required`): `{relay_id, adapter_type, handle, policy_count, monitors: {active, paused, suspended}, attention}` or null (`monitors` / `attention`: 3d-ii, below), computed in memory by `RelayService.refresh_summary` (no Slack calls) after policy/handle edits, session create/config write/start, template/profile saves (iterating dependent sessions), relay delete and at startup; broadcast only on change.
- **Audit viewer** on Library -> Relays reads `GET /api/relays/{id}/audit`; deep link `/settings/relays?session=<id>`.

### Relay inbound: Socket Mode self-DM instructions (inbound stage 3a)

Stage 3a lets the owner instruct a session from Slack: a top-level message in the owner's own self-DM that
contains `!handle` is delivered to that session; replies in the resulting thread need no handle. Monitors (3b),
digests / unread (3c) and the inbound UI / audit view (3d) are later stages. Code: `backend/relay/inbound/`.

- **Transport**: `SocketTransport` protocol (`transport.py`); the real one (`slack_socket.py`) uses `slack-sdk`'s aiohttp
  `SocketModeClient`, imported lazily in `connect()` (missing library -> status `transport_unavailable`, the Backend runs on).
  The app-level token is read via `RelayManager.get_token(relay_id, "app")` at connect time only. The SDK's own endless
  reconnect loop is disabled for URL-issue failures (`issue_new_wss_url`): the client is closed and the supervisor
  (`service.py`) applies backoff 1 -> 60 s with jitter, >= 5 s between attempts, reset after 60 s stable. An auth failure
  (`invalid_auth`, `token_revoked`, ...) -> `auth_failed` and NO retries until credentials change. One Backend per app-level
  token (Slack splits events between connections; documented in the setup guide and the status tooltip).
- **Status** (`GET /api/relays/{id}/inbound/status`, also `inbound_status` on the relay record): `disabled` (owner switch off
  or not configured: no app token / user token / owner / self-DM) | `transport_unavailable` | `connecting` | `connected` |
  `backoff` | `auth_failed` | `stopped`. `inbound_enabled` (default true) is the owner's switch (Library -> Relays chip + toggle,
  one Save). Saving credentials, Test connection, OAuth, a relay update or delete restart that relay's service.
- **Trust rule** (`router.py`, pure): an event is an instruction candidate only if its channel is the relay's resolved
  self-DM, the sender is the owner, it has no `app_id` / `bot_id` / agent metadata, no agent marker, and `channel_type == "im"`.
  Everything else (other users, channels, bots, the session's own replies, typed-marker messages) is dropped and counted.
  Edits, deletes and every subtype except none / `file_share` / `thread_broadcast` are dropped before dedupe, so an edit never
  re-triggers an instruction. Top-level: `!handle` is matched case-insensitively outside Slack angle tokens (`<!here>`),
  reserved words ignored, the handle removed from the delivered text; 0 handles ignored, an unknown handle / two distinct
  handles / empty instruction get ONE fixed marked reply (nothing from Slack is echoed). Thread replies route by an owned thread
  record (`!other` inside is plain text, D5). Files are not delivered (noted in the block).
- **Delivery**: `relay/inbound/sink.py::enqueue_external_comm` (bound by `InboundManager.bind(coordinator)`) -> `DeliveryGate.deliver` as an `EXTERNAL`, `WHEN_IDLE`, `auto_start`
  comm (`from_user=True`, `CommType.SYSTEM`: busy sessions wait, stopped ones start, a paused queue keeps it pending; an
  external instruction never implicitly resumes a queue). The comm id is deterministic (`uuid5` of relay:channel:ts) and an item
  the in-memory pending store already holds is not enqueued twice. The text is an `OWNER INSTRUCTION` nonce block
  (`wrap_instruction`) plus one closing line: `Slack thread: channel=… thread_ts=…. Answer with the relay `reply` tool using exactly this channel and
  thread_ts; replies in this thread need no approval.` (issue #2364 dropped the "further messages are instructions" and "never write
  !handle" sentences: a reply containing `!handle` is refused by the trigger-syntax check, whose message states the reason and the fix).
- **Received reaction (issue #2364)**: when an instruction has passed every check (trust rule, handle/thread resolution, age window,
  rate limits, session availability, relay attached) and is about to be enqueued, the RELAY adds `INBOUND_ACK_REACTION` (`"flying_saucer"`, 🛸, a reaction the owner rarely uses by hand so it is not mistaken for their own;
  Slack name without colons, `inbound/constants.py`) to the owner's message through the adapter (`reactions.add`, owner user token; no
  agent, gate or session involvement, whatever the session state). Scope: top-level instructions and thread follow-ups dispatched as
  instructions, in the self-DM, and nothing else: no reaction for ignored/dropped events, unknown/ambiguous/empty handle, late,
  rate-limited, deduped/replayed, session-unavailable or refused instructions, or any other channel/person, and never for the monitored
  content of stage 3b (`InboundService._ack_received` is only reachable from the instruction path and guards on kind + self-DM). The call
  is a tracked background task (10 s timeout `ACK_TIMEOUT_SECONDS`, no retry, `ratelimited` is not retried): Slack can neither delay nor
  fail the enqueue. At most once: an `acked` flag on the in-flight entry is fsynced BEFORE the Slack call, so restarts, in-flight
  re-runs and reconnect backfill never add it again or re-add one the owner removed (a crash in between loses the reaction, never doubles
  it); `already_reacted` counts as success. If the enqueue then fails (the refusal reply path), the reaction is removed best effort
  (`reactions.remove`, only if our add succeeded). Failures are audited, never block: `inbound_ack`.
- **What `from_user=True` + `CommType.SYSTEM` change (versus the plan's `from_user=False` / `TASK`)**: `Comm.validate()` requires exactly one
  source (a minion or the user), so `from_user=False` with no minion was not constructible; the owner's own instruction is "from the user",
  tagged `sender_type=EXTERNAL`, `sender_id=relay_id`. Effects, each pinned in `test_relay_inbound_integration.py`: (1) `_is_authorized` is true
  but `SYSTEM` is not a resume trigger (TASK/QUESTION/PIVOT only) and the delivery is `WHEN_IDLE`, so a paused queue keeps the instruction
  pending and `queue_paused` is never written; (2) sender-scoped flush treats it as a USER item: a user-originated PIVOT/HALT cancels a still
  pending Slack instruction exactly as it cancels a queued manual message, a minion's does not, and the stale-pivot note says "the user";
  (3) `send_message` is called with `inject_timestamp=False` and the dispatch metadata `{"relay_inbound": ...}` (no `comm` key, so the timeline
  shows an ordinary user turn with the instruction block, not a CommCard); (4) the gate writes no session fields (no last-input / queue
  bookkeeping); busy sessions wait for idle and stopped ones are auto-started like any `WHEN_IDLE` item. While waiting it appears in the
  session's Queue panel like a queued message (shown with the raw instruction text).
- **Acknowledgement**: at-most-once with a persisted in-flight list (`inbound_state.json`, fsync BEFORE the Slack ack). The
  in-flight list protects only the window between the ack and the enqueue: events accepted but not yet enqueued are re-run at
  startup. Once enqueued, an instruction is delivered from the relay's point of view.
- **Known limitation (pre-existing queue behavior, issue #2362; deliberately not fixed in 3a)**: the pending queue is not
  recovered across a Backend restart (`PendingStore.load` has no production caller and nothing re-arms the when-idle drain at
  startup). **An instruction queued for a busy or stopped session is lost if the Backend restarts before it is drained**; the
  owner may need to resend it. 3a adds no `recover_pending`, no startup drain and does not touch `PendingStore` / `DeliveryGate`.
- **Known limitation (3a scope, carried into 3b/3d planning): no delivery feedback after the enqueue.** Once an instruction is enqueued it counts as
  delivered, so if the delivery gate later fails it (auto-start error, session in ERROR, send failure) nothing is posted back to Slack; the
  failure is only visible in the session's Queue panel (status `failed`). Refusals the pipeline can decide before the enqueue (unknown handle,
  ephemeral / error / detached session, late) do get a marked reply. A pending instruction shows in the Queue panel as `Slack: <preview>`.
- **Window and loops**: an instruction older than 15 minutes when processed (`INSTRUCTION_MAX_AGE_SECONDS`) is NOT executed and
  gets one "received late" reply. First start sets the cursor to now (no backfill before enablement); every `hello`
  backfills the self-DM since the cursor and owned-thread replies (3 pages max, truncation audited). Accepted instructions are
  rate limited (30/min burst 10 per relay, 10/min per thread; over the limit -> dropped, audited once a minute, no reply). Relay
  replies carry the marker, agent metadata and are checked with `contains_trigger_syntax`; a session's output can never
  instruct another session.
- **Instruction threads (D3/D4/D6)**: records (`kind=instruction_thread`, `subscriptions.json`) are created ONLY by the pipeline for a
  validated top-level owner instruction (`origin_ts == thread_ts`); no tool, route or config can create one. Expire after 14 days
  idle, cap 200 per relay (oldest idle evicted). Session delete drops its records; dispose suspends them (one "no longer active"
  reply to a follow-up) and the next start restores them; reparent changes nothing.
- **Thread exception in the policy gate** (`RelayPolicyGate._owned_thread`, one decision path for the pre-hook and the handler):
  `reply` / `post` / `upload_file` with `thread_ts`, and `read_thread`, targeting an ACTIVE thread record owned by the CALLING
  session in the relay's self-DM upgrade an effective policy of unset or `ask` to `auto` (audit `approval=thread_grant`). An
  explicit `blocked` / `read_only` on the self-DM wins. `react`, `mark_read`, `search`, other threads / channels / sessions and
  the trigger-syntax refusal, marker, footer and metadata paths are untouched. Switching `inbound_enabled` off disables it.
- **Persistence** (`data/relays/{relay_id}/`): `subscriptions.json`, `inbound_state.json` (cursors, 5000-entry dedupe ring,
  in-flight list), atomic tmp + fsync + replace; a corrupt file is moved aside (`*.corrupt-<ts>`) and the service starts empty.
- **Audit** (existing relay audit log): `inbound_instruction` (`delivered|refused|late|rate_limited|deduped|error`, with `code`),
  `inbound_ack` (received reaction: `ok|error|removed`, sanitised `code` such as `already_reacted`, `message_not_found`, `timeout`; `channel_id`,
  `message_ts`, `session_id`, `handle`), `inbound_reply` / `inbound_reply_failed`, `inbound_connection` (`inbound_status`, `code`), `inbound_drop_summary` (`reason_counts`
  every 5 minutes), `inbound_housekeeping` (expiry / eviction / `backfill_truncated`).
- **Lifecycle**: `InboundManager` (`RelayRuntime.inbound`) starts last in coordinator `initialize()` and stops first in `cleanup()`
  (before sessions terminate, state flushed). Dependencies: `slack-sdk>=3.33,<4` (MIT), `aiohttp>=3.9` (Apache-2.0).

### Relay inbound: monitors (3b-i)

Stage 3b-i lets a session watch Slack threads and channels. Mentions and DM monitors, the consent prompt and the REST surface
are stage 3b-ii (below); the panel is 3d; digests/unread are 3c. All monitored content is **untrusted**, never an instruction.
Code: `relay/inbound/{matcher,coalescer,flood,content,watcher,monitor_delivery}.py`, `mcp/relay_monitor_tools.py`.

- **Tools** (`mcp__relay_slack__*`, `AdapterSpec.monitor_tools`, audited, not send tools): `monitor_thread(channel, thread_ts, expires_at?)`,
  `monitor_channel(channel, keywords?[], users?[], expires_at?)` (>= 1 filter; keywords OR, users OR, both groups AND; NFKC + casefold
  substring), `unmonitor(monitor_id)`, `list_monitors()` (own monitors only). Creation is a **read** of the channel for the policy
  engine (blocked refused, unlisted -> choose-policy prompt, `dontAsk`/unattended -> `policy_unset(_dont_ask)`), then: self-DM (any spelling)
  -> `self_dm_not_monitorable`, other DMs -> `dm_not_monitorable_here` (both before any prompt), owner not a member -> `not_a_member`
  (`conversations.info.is_member`; unknown -> allowed with a note), inbound off / no app token -> `inbound_unavailable`,
  caps 20 per session / 100 per relay -> `monitor_limit`, no filter -> `filter_required`, bad filters -> `invalid_filter`, an identical
  monitor returns the existing one (a flood/delivery-failed one is resumed). `expires_at` <= 90 days. Sessions started before the
  upgrade lack the tools (static tool list): **restart them**.
- **Store**: records of kind `monitor_thread` / `monitor_channel` in `subscriptions.json` (same file as 3a threads, version 1):
  `status active|suspended`, `suspended_reason` (`channel_blocked|policy_unset|disposed|session_inactive|flood|delivery_failed`), owner
  `paused` (a separate flag, never touched by dispose/start), durable `last_seen_ts`, counters. Index by channel (the event handler never scans).
  Thread monitors expire after 14 days without activity.
- **Matching** (`matcher.py`, pure): never content = the self-DM in any thread, no sender, replies in an instruction thread, the **echo rule**
  (own metadata marker `cc_webui_agent_message`, `app_id == own api_app_id`, text agent marker). `api_app_id` is learned from the first live
  event's `event_callback.api_app_id` and persisted in `inbound_state.json`; **until learned only the metadata and text markers apply** (not
  confirmed against a recorded real event in this stage). Other bots are delivered flagged `bot: true`. Edits/deletes/other subtypes never
  reach monitors, so an edit is not redelivered. Backfill of a channel monitor covers top-level messages (`conversations.history`).
- **Dedupe ring (changed from 3a)**: the ring is marked only for instruction candidates and monitor-matched events; an unmatched event
  is never remembered, so unrelated traffic cannot evict instruction keys.
- **Coalescer** (`coalescer.py`): per-session in-memory buffer, 60 s window from the first message, one envelope: <= 50 messages /
  24 000 characters (each message cut at 2 000), the rest `+N more (by channel IDs)`. Ingress limits: 120 matched messages / 10 min per
  monitor, 600 / 10 min per relay (dropped messages are counted, audited once per window and covered by the cursor); 3 consecutive flooded
  windows suspend the monitor (`flood`). A session with a pending monitor envelope or 12 envelopes in the last hour has the flush
  postponed in 60 s steps up to 10 min. Stopping discards buffers without enqueue.
- **Review hardening**: the echo rule also drops a message FROM THE OWNER carrying an `app_id`/`bot_id` (our app acts with the owner's token), so
  it holds before `api_app_id` is learned (a post without any of these, e.g. an `upload_file` share, cannot be recognised outside the self-DM). Per-monitor
  recent-key dedupe (1000 keys) replaces ring marking for backfilled messages, so a backfill of one monitor never hides a message from
  another. One flush per session at a time; with >= 3 queued undispatched envelopes (stuck/paused session) the flush keeps waiting. Envelopes are
  watched from before the enqueue: **blocking the channel (or removing the monitor) cancels a queued, not yet dispatched envelope** carrying its
  content (ledger `cancelled`; not a failure; the other monitors of that envelope re-collect from their cursor); an envelope already dispatched
  cannot be recalled. A retry of an envelope whose earlier comm ended (failed/cancelled) gets a new comm id (`attempt` suffix). The relay-wide
  flood budget (600/10 min) drops messages but never counts toward one monitor's flood suspension. `dm`/group-DM conversations are refused
  (`dm_not_monitorable_here`). `InboundManager.refresh` (credential / relay change) restarts the service: envelopes then in flight are no longer
  watched and the next backfill may repeat them (at-least-once).
- **Envelope text** (`content.py`): outside the fence only our own data (fixed framing, counts, IDs); entries (text, names, labels, permalink) are
  inside `wrap_untrusted`; `comm.content`/queue label is counts only (`Slack monitor: N messages`).
- **Comm shape**: `Comm(from_minion_id=SYSTEM_MINION_ID, from_user=False, sender_type=EXTERNAL, sender_id=relay_id, comm_type=INFO, WHEN_IDLE,
  auto_start=True)`, created only when there is content, addressed to the monitoring session only (never a parent/overseer). Consequences
  (pinned in `test_relay_monitor_integration.py`): a user PIVOT/HALT does **not** cancel pending monitor content (it still cancels a pending 3a
  instruction, which is a user comm); SYSTEM is nobody's ancestor so it can never HALT/PIVOT or resume a paused queue; the timeline shows a
  collapsed CommCard ("Slack monitor", no jump link) and the queue panel `Slack monitor: N messages`. `PendingStore`/`DeliveryGate` are unchanged.
- **Cursor-on-confirm** (`watcher.py`): the in-memory `high_water` advances at buffering, the durable cursor only when the watcher (5 s poll of
  the pending ledger) sees the envelope `delivered`. After a failed/cancelled envelope the cursor is frozen for that monitor until a backfill
  re-collects it; 3 consecutive failures suspend (`delivery_failed`); a vanished ledger item is "unknown" (cursor stays, no failure count).
  A Backend restart resets `high_water` to the durable cursor and the reconnect backfill (24 h cap, 3 pages x 100, `older_omitted` notice)
  re-delivers lost content: **at-least-once, a repeat is possible** (messages carry `ts`). The queue gap of #2362 is unchanged.
- **Policy at delivery**: triggers (`RelayService.change_listeners`: session policy edits, tier edits, attachment/config sync, plus
  session start and service start) re-evaluate; the **flush re-checks fresh**. Blocked -> suspended `channel_blocked`, no policy ->
  `policy_unset`, relay detached/session errored/ephemeral -> `session_inactive`; becoming readable again resumes **from now** (no backfill
  of the gap). Dispose suspends only ACTIVE records (`disposed`), start restores only those, re-checks policy and backfills the disposal period;
  delete drops monitors, threads, buffers and watches; reparenting changes nothing.
- **Instruction failure replies**: the same watcher follows 3a instructions: `failed` -> one marked `delivery_failed` reply, `cancelled`
  (queue cleared) -> one marked `cancelled` reply, in-memory once-guard, the received reaction stays, none after a Backend restart
  (nothing is watched; #2362). Monitored content never produces a reaction or a Slack post.
- **Audit**: `monitor_created|removed|suspended|resumed|delivered|delivery_failed|expired|flood|backfill`, instruction outcomes
  `delivery_failed|cancelled`; new fields `monitor_id, monitor_kind, envelope_id, omitted`. No message text, ever; keyword filters
  are stored as sha256 (`query_sha256`) + count only. Unmatched-event drops join the 5-minute `inbound_drop_summary` (`monitor_*` reasons).

### Relay inbound: mentions and DM monitors (3b-ii)

Stage 3b-ii adds two **scope monitors** that have no channel: `monitor_mentions` (every message that mentions the owner,
`<@OWNER>` / `<@OWNER|name>` anchored on the id; `@here` / `@channel` / user groups do not count) and `monitor_dms` (direct
messages and group DMs/MPIMs other people send the owner). Same record schema (`channel`/`thread_ts` null), coalescer, flood
limits, envelope, Comm shape and cursor-on-confirm as 3b-i; optional keyword / sender filters as `monitor_channel`. Counts toward
the 20 / 100 caps. Consent UI, digests (3c) and the monitors panel (3d) are other stages. Code: `relay/inbound/{backfill,hydrate,
public,search_limiter}.py`, `mcp/relay_monitor_tools.py::handle_scope_monitor`.

- **Consent (`monitor_scope`)**: `AdapterSpec.scope_tools`; `RelayPolicyGate.evaluate` has an early branch (before channel resolution
  and the policy matrix) that returns a PROMPT carrying `{kind: "monitor_scope", scope, filters_summary, relay_name, text}` with the
  fixed plain-language wording (mentions: every message that mentions you, in any conversation, untrusted content, blocked channels stay
  excluded; dms: direct and group messages other people send you, own messages and self-DM excluded). One prompt per creation; it never
  looks at the permission mode, so **bypass still prompts**; unattended sessions get `monitor_consent_unavailable`, don't-ask sessions
  `monitor_consent_unavailable_dont_ask` (nothing created, nothing waits). `permission_bridge.apply_response` answers Allow / Deny only
  (never a policy write, whatever `relay_choice` carries). An **identical existing monitor returns `already_monitored` with no prompt** in every mode (the pre-hook asks `InboundManager.scope_monitor_exists`; filters needing name resolution always prompt) and consumes a leftover callback grant. Pausing or deleting a monitor also cancels its queued, undispatched envelopes.
  The record stores `consent: {at, via: "prompt"}`; the audit row has `approval=prompt_allow`. `RelayPermissionPrompt.vue` has the
  third kind (Allow / Deny only). Revoking consent = the owner deletes or pauses the monitor (REST).
- **Policy semantics**: no policy is read at match time. At **flush** (fresh policy) messages from an explicitly `blocked` channel are
  dropped for scope monitors (counted `monitor_blocked_channel`, cursor still advances, the monitor stays active); unlisted, read_only,
  ask and auto channels are delivered (the consent covers them). Accepted residuals: a channel blocked after matching does not recall an
  envelope that is already queued and undispatched; blocked-channel messages are filtered after the flood limits have counted them, so a very
  noisy blocked channel can consume the relay-wide flood budget (no I/O is allowed at match time). Channel / thread monitors keep the 3b-i suspend rules.
- **Matching**: `matcher.matches_monitor(event, monitor, owner_id)`; the owner's own messages never match; `never_content` (self-DM,
  echo rule, instruction threads) applies first; DM monitors need `channel_type` im/mpim (backfill sets it from the conversation kind);
  without a known owner scope monitors never match. `_leaf_text` renders rich-text `user` elements as `<@ID>` so a block-only mention is
  detectable.
- **Backfill** (every `hello`, session start; `InboundService.backfill_monitors`): **DMs/MPIMs** one `users.conversations` call per run
  (self-DM removed, at most 50 conversations checked, `conversation_cap` counted), per conversation (serialized) `conversations.info` newest
  ts decides whether a history fetch is needed (unknown `latest` fetches; a conversation that answers `channel_not_found` / `user_disabled` / `is_archived`, e.g. a DM with a deactivated user, is skipped without a gap notice, counted `conversation_gone`). **Mentions**: `search.messages "<@OWNER>"` newest first
  (`sort=timestamp&sort_dir=desc`, <= 3 pages, **no date filters**: the window is re-read from `cursor - 60 s`), spaced >= 6 s per relay
  (`SearchLimiter`); hits from conversations the owner is not a member of, the self-DM and the owner's own are dropped (`non_member`);
  if the membership list cannot be fetched the run fails closed. **Every remaining hit is hydrated** (single-message fetch with
  `include_all_metadata`, via `conversations.replies` when the hit's permalink carries `thread_ts`) and judged by the live echo rule
  (metadata marker, own `api_app_id`, owner message with app/bot id, text marker) plus the mention regex on the HYDRATED text before it can
  be delivered. Cap 20 hydrations per run, serialized, a per-run cache; any error / rate limit / missing message drops the hit
  (`hydration_failed`, `hydration_cap`, fail closed; each also raises the gap notice, because a hit older than the cursor margin is not re-read later). DM / group-DM backfill uses `conversations.history`, which excludes thread replies: replies in a DM missed during a disconnect are not backfilled (live delivery is unaffected; not covered by the gap notice). No sent-message ledger. One delayed re-run ~30 s later (search index lag), idempotent
  through the per-monitor keys. Rate limits / caps / failed calls produce a **gap notice** (one line outside the fence on the next envelope
  of the session: "Slack backfill was incomplete for the period T0-T1 …"; audited `monitor_backfill` `code=gap_notice` with `reason_counts`).
- **Duplicates**: the last 100 delivered keys per monitor are persisted with the cursor (`recent_keys`, debounced) and seeded at service
  start, so the 60 s margin does not repeat messages after a restart. Residual: at-least-once, a repeat is possible after a crash between
  delivery and the debounced write (messages carry `ts`).
- **REST** (`routers/relay.py`, public shape `inbound/public.py`): list / `PATCH {paused}` / `DELETE` relay-qualified
  (`/api/relays/{id}/monitors[/{monitor_id}]`) and session-qualified (`/api/sessions/{sid}/relays/{rid}/monitors[/{monitor_id}]`);
  404 unknown relay/session/monitor, 409 relay not attached / inbound not running, 422 body. Resume restarts the cursor from now and does
  not lift a system suspension. Owner actions audit with `source=owner` (`monitor_paused|resumed|removed`). The public shape carries labels
  (plain text) and counters, never message content. Dispose suspends scope monitors, start restores them (owner pause survives), delete
  drops them.
- **Not changed**: `PendingStore` / `DeliveryGate` (#2362: the pending queue is still not recovered across a restart; mitigated for
  monitors by cursor-on-confirm). Sessions started before the upgrade lack the two tools (static tool list): restart them. `api_app_id`
  placement on a real event is confirmed only by the live validation recorded in the PR.

### Relay inbound: Inbound tab (3d-i)

Library -> Relays (`/settings/relays`) has tabs `Setup | Inbound | Audit` (`?tab=`; a legacy link with only `session` / `channel` /
`events` opens Audit). Panels stay mounted (`v-show`), so the credentials form and its dirty guard survive a switch. The Inbound tab
shows **Health** (connection, monitors, last 24 h, attention tags), a relay-wide **Monitors** table (pause / resume / delete are
immediate, "Applies immediately", optimistic with rollback; resume never lifts a system suspension) and **Recent activity** (last 50
inbound / monitor audit rows). The per-session surface is 3d-ii. Code: `relay/inbound/health.py`, `components/relay/RelayInbound*.vue`.

- **Health** (`GET /api/relays/{id}/inbound/health`): connection facts + monitor counts + audit-derived 24 h counters + `attention[]`.
  Never a Slack call, never message text. The 24 h counters come from the AUDIT (`RelayAuditLog.scan_since`: newest first, early exit at
  the window start, <= 5000 rows read -> `truncated` means "at least", cached 30 s per relay with single flight, JSON parsed OUTSIDE the
  audit lock so writes are never blocked), so they survive a restart; only live-only facts stay in memory (connection state, in-flight,
  `last_backfill` detail, labelled "since restart"). `status_since` is seeded from the audit after a restart. `GET .../audit` gained
  `events` (exact names, trailing `*` prefix), `since` and `outcome`.
- **Attention rules** (constants in `health.py`, one unit test per rule): attention level (amber dot on the Inbound tab) = `auth_failed`,
  `transport_unavailable`, `reconnecting_long` (> 2 min), `inbound_off_with_monitors`, `monitors_flood`, `monitors_delivery_failed`,
  `monitors_policy_unset`, `monitor_error`, `backfill_gap_24h`, `backfill_cap_24h`, `instruction_delivery_failed_24h`; info level (plain
  tag, no dot, owner-caused or self-healing) = `monitors_channel_blocked`, `monitors_disposed`, `monitors_session_inactive`,
  `monitors_paused`, `monitors_expiring`. A monitor already suspended for `delivery_failed` is not also reported as `monitor_error`.
- **Public monitor rows** gained `last_seen_ts` ("caught up to"), `last_activity_at`, `failures`, `flood_windows`.
- Everything Slack-controlled (labels, keywords, names) is rendered with text interpolation only; polling (health + monitors, 10 s) runs
  only while the Inbound tab is visible and the document is not hidden; the activity feed loads on show / Refresh and never polls.

### Relay inbound: session surface (3d-ii)

Stage 3d-ii puts the monitors on the session. Code: `relay/inbound/health.py` (shared rules), `relay/service.py`, `relay/inbound/{service,manager,store}.py`;
frontend in `frontend/CLAUDE.md`.

- **`relay_summary` gained `monitors: {active, paused, suspended}` and `attention: bool`**, in memory (no Slack call, no audit scan).
  `RelayService.monitor_summary` is an injected sync callable (`InboundManager.session_monitor_summary`, wired in `coordinator_builder`;
  default zeros) so `relay/service.py` imports nothing from `relay.inbound`. `attention` comes from `health.attention_for_session`, which
  uses the SAME primitives as `build_health` (`connection_codes`, `monitor_needs_attention` / `SUSPENSION_RULES` severities,
  `monitor_is_erroring`), so the session chip and the Inbound tab cannot disagree (a parity test runs every connection x monitor-state
  combination through both). A session is lit only if it HAS monitors and one of its monitors is suspended for `flood` / `delivery_failed` /
  `policy_unset`, or is active with `failures > 0` and a `last_error`, or the connection is attention-level (`auth_failed`,
  `transport_unavailable`, reconnecting > 2 min, inbound off / missing token while it has active or paused monitors). Instruction-only
  sessions, owner-paused monitors and info-level suspensions (`channel_blocked`, `disposed`, `session_inactive`) stay quiet. The 24 h audit
  items (`backfill_gap_24h`, ...) are audit-derived and Library-only. Residual: after a restart "reconnecting too long" is measured from
  the service start, not from the audit-seeded time the Library health uses.
- **Refresh triggers** (each calls `RelayService.refresh_summary`, which broadcasts only when the summary changed): every monitor mutation
  through `JsonSubscriptionStore.on_monitors_changed` (create / resume-by-agent, pause / resume, remove, suspend / policy resume, session
  dispose / start / delete, expiry, and `touch_monitor` writes of `failures` / `last_error`; plain cursor / counter writes do not),
  an inbound status change (only the sessions that hold monitors, at most the 100-per-relay cap), a one-shot timer when a reconnecting
  spell starts (`attention_sleep`, fires at the 2 min threshold), service start (the startup summaries were computed before the store
  loaded). Fake relay services in tests need `refresh_summary`.
- **`envelope_id`** (the comm id) is in `metadata.relay_inbound` of monitor envelopes AND instructions, and in the instruction audit rows
  (monitor audit rows already had it). No UI link yet.
- **Test-only seeding** (`backend/tests/relay_inbound_demo.py`) also appends genuine `relay_inbound` user messages (instructions + a monitor
  envelope, produced by a real `InboundService` through the real `DeliveryGate`, stored with `MessageRecord.from_user_input`) to the seeded
  sessions' `messages.jsonl`.

### Relay inbound: scheduled digest monitors (3c-i)

Stage 3c-i adds `mode="digest"` to the four monitor kinds (thread, channel, mentions, DMs): the optional `schedule`
parameter of `monitor_thread|monitor_channel|monitor_mentions|monitor_dms` turns a live monitor into a DIGEST: ONE
message at the scheduled times, only when there is content. Unread monitors (3c-ii) and the UI / attention / owner
run-now (3c-iii) are later stages. Code: `relay/inbound/{schedule,digest}.py`, `backfill.py::collect_thread_or_channel`,
`service.py::_enqueue_envelope`.

- **Schedule** (`schedule.py`, pure): `{"daily": {"times": ["09:00","17:00"] (1-4, 24 h), "days"?: [mon..sun | 0-6],
  "timezone"?: IANA}}` or `{"every_minutes": N}` (30-1440); exactly one form, unknown keys -> `invalid_schedule`. Timezone is
  resolved at creation: explicit -> the owner's Slack tz (`users.info.tz`, cached 1 h in `InboundService.owner_tz`) -> UTC
  fallback. **Tool results, `list_monitors` and the consent prompt always state the schedule in words, the timezone and its
  source (`slack|explicit|utc_fallback`) and `next_run` (UTC + local)**. `next_run_after` is strictly after the given instant;
  a nonexistent local time (spring-forward) runs at the first valid instant after the gap, an ambiguous one (fall-back) at
  its first occurrence; a deterministic 0-90 s per-monitor jitter is added to the slot.
- **Record**: `mode`, `schedule`, persisted `next_run`, `last_run_at`, `last_run {at,outcome,messages,omitted,gap,late}`,
  `run_failures`, `slot_retries`, `slot_defers`, `last_run_trigger`, `last_manual_run_at`. Identity gains `(mode, schedule)`
  (anchor and tz source do not count): a live and a digest monitor of one scope coexist, an identical digest is
  `already_monitored` (no consent prompt for scope kinds). `_reindex` skips digests (**they never match live events**), the
  `hello` / session-start backfill skips them (the scheduler is their catch-up). Caps: the shared 20 / 100 plus
  `MAX_SCHEDULED_PER_RELAY = 30`. The 14-day thread idle rule does not apply to digests; `expires_at` does.
- **Scheduler** (`DigestScheduler`, 30 s tick, own sleep): due = active, unpaused, `next_run <= now` (never early); oldest slot
  first, <= 3 runs per tick, serialized relay-wide; `next_run` is recomputed and fsynced BEFORE the run (a crash never re-runs
  a slot); several missed slots produce ONE run (`late` when > 2 ticks overdue, audit code `late`); a 30 s startup delay after
  the service starts. Digests need only the user token and the owner's `inbound_enabled` switch (`digest_eligibility`), **not**
  the Socket Mode connection or app token. Pause/resume and policy resume recompute `next_run` after now and start the cursor
  from now; dispose suspends, start restores and triggers a due-check.
- **A run** (`_run`): fresh `apply_policy` (blocked / unset suspend), window `max(cursor, now - 72 h)` (`older_omitted`), the
  existing strategies through a collecting sink (`BackfillRun.digest/sink/hydration_cap/pacer`), shared Tier-3 spacing
  (`api_limiter`, >= 1.3 s) plus the 6 s search limiter, mentions hydration cap **40** (hits beyond it are never delivered; the
  next envelope's line outside the fence states the counts, e.g. "12 mentions could not be checked (hydration limit)"; audit
  `reason_counts`). **Empty -> no envelope, no session start**, the cursor moves to run start - 60 s. Content -> ONE envelope per
  monitor run (same caps as live, `+N more` tally, blocked-channel messages dropped for scope digests) through the shared
  `_enqueue_envelope` (Comm shape unchanged, `relay_inbound.mode="digest"`, CommCard "Slack digest", queue label
  `Slack digest: N messages`, framing "scheduled digest ... untrusted"). Cursor-on-confirm, 3 failures -> `delivery_failed`.
- **Defer / rate limits**: a session already holding `MAX_PENDING_ENVELOPES` monitor comms defers the run 10 min, up to 6 times,
  then enqueues anyway (audited each time). A `ratelimited` anywhere stops the run: rescheduled +5 min up to 3 times per slot
  (audit `rate_limited_rescheduled` + `retry`), then the slot is given up (`rate_limited_gave_up`), next regular slot and a gap
  notice on the next envelope.
- **`run_monitor_now(monitor_id)`**: own digest monitors only; refusals `monitor_not_found`, `not_a_digest_monitor`,
  `monitor_paused`, `monitor_suspended`, `run_too_soon` (agent 5 min; owner 60 s in 3c-iii), `run_in_progress`,
  `inbound_unavailable`. Returns at once (the run is a background task serialized with the loop), never changes `next_run`,
  every attempt is audited (`monitor_digest`, `source=agent|owner`).
- **Consent**: channel / thread digests follow the channel read policy exactly as live monitors; mentions / DMs digests ask
  once (`monitor_scope`, the text and `prompt.schedule` name the digest schedule); an identical existing digest returns
  without a prompt (`InboundManager.scope_monitor_exists` parses the schedule with the cached owner tz; unknown tz -> prompt).
- **Audit**: `monitor_digest` (`outcome` delivered|empty|rate_limited|deferred|refused|failed|accepted, `code`, `count`,
  `omitted`, `reason_counts`, `envelope_id`, `retry`, `source` schedule|late|agent|owner); `monitor_created` carries
  `code=digest`. No message text.
- **Known limitations**: #2362 (the pending queue is not recovered across a restart; the cursor moves only on confirmed delivery so
  the next digest re-collects, at-least-once); a digest and a live monitor on the same scope repeat messages by design (tool
  descriptions and the creation result say so); two Backends with one app token disturb live monitors, not digests, but each
  runs the digests of its own monitors; edits are not redelivered; Slack search lags (60 s margin); sessions started before
  the upgrade lack the `schedule` parameter and `run_monitor_now` (static tool lists): restart them.

#### Relay setup (owner walkthrough)

1. Settings -> Library -> Relays -> **Create Slack relay**.
2. Open **Slack app setup**, copy or download the manifest, create a Slack app "From a manifest", install it to the workspace.
3. Create a Generic secret in Library -> Secrets for the User OAuth Token (`xoxp-`) and, for the inbound stage, one for an app-level token with `connections:write` (`xapp-`).
4. Back on Relays pick both secrets, **Save credentials**, then **Test connection**.
5. In a profile, template or session editor open **Relay**, pick the relay, adjust the footer, optionally set the handle.

Secret references are the primary path; OAuth needs an https or localhost redirect URL.

Slack's 2026 API terms (retention, Marketplace distribution) are still to be reviewed before release.

## Verifying Configuration

The injected env dict is logged at debug level by `claude_sdk.py` when `--debug-sdk` is enabled.
Inspect `data/logs/sdk_debug.log` for the `ClaudeAgentOptions: ...` line to see the final env
dict for each session.

## Auditing Future SDK Versions

When upgrading `claude-agent-sdk`, check for new env vars by:
1. Grepping the installed package for `os.environ`, `os.getenv`, `CLAUDE_*`, `ANTHROPIC_*`.
2. Checking the bundled CLI binary for new `CLAUDE_CODE_DISABLE_*` flags.
3. Updating `_BACKGROUND_CALL_ENV_MAP` in `backend/claude_sdk.py` and `BackgroundCallsConfig` in `backend/config_manager.py`.
   (Opt-in cascade fields, see issue #2287, are not background-call suppressions and stay out of that map.)

# Frontend Architecture - Vue 3 + Pinia + Vite (PRODUCTION)

## Current Status

The Vue 3 migration is **complete** and in production use. The frontend has grown significantly beyond the original migration scope with 13 Pinia stores, 99+ Vue components, 22 tool handlers, and 9 composables.

**Documentation**: See [frontend/CLAUDE.md](./frontend/CLAUDE.md) for detailed frontend architecture.

## Frontend Structure

```
frontend/
├── src/
│   ├── stores/                    # 13 Pinia stores
│   │   ├── session.js             # Session CRUD, selection, deep linking
│   │   ├── project.js             # Project hierarchy, ordering
│   │   ├── message.js             # Messages, tool calls, orphaned detection
│   │   ├── polling.js             # HTTP long-polling (UI + session event streams)
│   │   ├── legion.js              # Multi-agent: comms, minions
│   │   ├── ui.js                  # Sidebar, modals, loading, responsive
│   │   ├── queue.js               # Per-session message queue
│   │   ├── schedule.js            # Per-legion cron schedules
│   │   ├── resource.js            # Per-session resources (images/files)
│   │   ├── diff.js                # Per-session git diff data
│   │   ├── task.js                # Per-session SDK task tracking
│   │   ├── mcp.js                 # MCP server state (active servers)
│   │   └── mcpConfig.js           # MCP server configuration CRUD
│   │
│   ├── composables/               # 9 reusable composition functions
│   │   ├── useToolResult.js       # Shared tool result extraction
│   │   ├── useToolStatus.js       # Tool status computation
│   │   ├── useAgentColor.js       # Per-agent color assignment
│   │   ├── useLongPress.js        # Long-press gesture handler
│   │   ├── useMarkdown.js         # Markdown rendering with DOMPurify
│   │   ├── useMermaid.js          # Mermaid diagram rendering
│   │   ├── useNotifications.js    # Sound/browser notifications
│   │   ├── useResourceImages.js   # Resource image helpers
│   │   └── useTTSReadAloud.js     # Text-to-speech / read-aloud
│   │
│   ├── components/
│   │   ├── layout/        (13)    # ProjectPillBar, AgentStrip, AgentChip, RightSidebar, DeletedAgentsModal, etc.
│   │   ├── configuration/ (12)    # ConfigurationModal, McpConfigTab, McpServerPanel, FeaturesTab, ReadAloudTab, etc.
│   │   ├── project/       (4)     # ProjectOverview, ProjectCreateModal, etc.
│   │   ├── session/       (7)     # SessionView, SessionInfoBar, McpServerDetail, modals, etc.
│   │   ├── messages/      (12)    # MessageList, MessageItem, InputArea, SubagentTimeline, TruncationBanner, etc.
│   │   ├── messages/tools/ (6)    # ActivityTimeline, PermissionPrompt, TimelineNode/Detail/Segment/Overflow
│   │   ├── tools/         (22)    # Tool handlers: Read, Edit, Bash, Agent, SendComm, Task*, Skill, etc.
│   │   ├── legion/        (2)     # MinionTreeNode, MinionViewModal
│   │   ├── header/        (1)     # TimelineHeader
│   │   ├── statusbar/     (3)     # SessionStatusBar, TimelineStatusBar, RateLimitBadge
│   │   ├── schedules/     (3)     # SchedulePanel, ScheduleItem, ScheduleCreateModal
│   │   ├── tasks/         (6)     # TaskListPanel, DiffPanel, ResourceGallery, QueueSection, etc.
│   │   └── common/        (6)     # FolderBrowserModal, CommCard, DiffFullView, ResourceFullView, AttachmentChip, AuthPrompt
│   │
│   ├── router/                    # Vue Router: /, /project/:id, /session/:id, /session/:id/archive/:id
│   ├── utils/                     # API client, time formatting, tool summaries, file types, template vars
│   └── assets/                    # CSS (styles.css, tool-theme.css)
│
├── vite.config.js                 # Vite dev server + proxy + build config
├── index.html                     # Entry point
└── package.json                   # Dependencies (Vue 3.4, Pinia 2.1, Vite 7.1, Bootstrap 5.3)
```

## Development Workflow

### Running Frontend Dev Server

```bash
# Terminal 1: Backend (use port 8001 to avoid conflicts with production on 8000)
uv run python main.py --host 0.0.0.0 --debug-all --port 8001

# Terminal 2: Frontend dev server with HMR
cd frontend
npm install  # first time only
npm run dev  # starts on http://localhost:5173

# Access dev server at http://localhost:5173
# Changes reload instantly with Hot Module Replacement
```

### Production Build

```bash
cd frontend
npm run build  # Output: frontend/dist/

# Update FastAPI to serve frontend/dist/ instead of static/
# Delete static/ directory after stability verified
```

## Key Benefits Over Vanilla JS

1. **State Management**: 13 Pinia stores replace 135+ instance variables and dual Map+Array storage
2. **Automatic Reactivity**: No manual `renderSessions()` calls - Vue reactivity handles all UI updates
3. **Component Architecture**: 6767-line monolith split into 99+ focused, reusable components
4. **Event Listener Cleanup**: Automatic cleanup prevents memory leaks
5. **Developer Experience**: Instant HMR, Vue DevTools, TypeScript support, clear separation of concerns

## Pinia Stores (14 Stores)

For detailed store documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#pinia-stores-14).

### 1. Session Store (`stores/session.js`)
**Responsibility**: Session lifecycle, CRUD operations, selection

**State**:
- `sessions` (Map): All sessions by ID
- `currentSessionId` (ref): Currently selected session
- `inputCache` (Map): Preserved unsent text per session
- `initData` (Map): Session initialization config
- `deletingSessions` (Set): Track deletions in progress

**Key Actions**:
- `fetchSessions()`, `createSession()`, `selectSession()`, `deleteSession()`
- `startSession()`, `pauseSession()`, `terminateSession()`, `restartSession()`, `resetSession()`
- `updateSessionName()`, `setPermissionMode()`
- Deep linking with auto-start for created/terminated sessions

### 2. Project Store (`stores/project.js`)
**Responsibility**: Project hierarchy, organization

**State**:
- `projects` (Map): All projects by ID
- `currentProjectId` (ref): Currently selected project (for Legion views)

**Key Actions**:
- `fetchProjects()`, `createProject()`, `deleteProject()`, `updateProject()`
- `toggleExpansion()`, `reorderProjects()`, `reorderSessionsInProject()`
- `isMultiAgent(projectId)`: Check if project is Legion

### 3. Message Store (`stores/message.js`)
**Responsibility**: Messages, tool call lifecycle, orphaned tool detection

**State**:
- `messagesBySession` (Map): Messages per session
- `toolCallsBySession` (Map): Tool calls with full lifecycle tracking
- `toolSignatureToId` (Map): Tool identification for permission matching
- `permissionToToolMap` (Map): Permission request to tool mapping
- `orphanedToolUses` (Map): Tools marked as orphaned

**Key Actions**:
- `loadMessages()`: Tail-first window load (`{window: 'tail'}`) with orphaned detection
- `addMessage()`, `addToolCall()`, `updateToolCall()`
- `handleToolUse()`, `handlePermissionRequest()`, `handlePermissionResponse()`, `handleToolResult()`
- `toggleToolExpansion()`: Collapse/expand tool cards
- Orphaned tool detection: session restart, interrupt, termination

### 4. Polling Store (`stores/polling.js`)
**Responsibility**: HTTP long-polling (one multiplexed stream carrying UI + every subscribed session), outbound message dispatch

**State** (Epic #1990 stage 5c: one shared `StreamConnection` state machine for the whole
multiplexed transport — states `idle | connecting | connected | resyncing | backoff |
unreachable | archived`, liveness derived only from poll responses/fetch outcomes, no
wall-clock staleness timers):
- `uiState`/`uiRetryCount`/`uiBackendStatus` and `sessionState`/`sessionRetryCount`/
  `sessionBackendStatus`: same underlying shared connection, exposed under both names for
  API-shape compatibility (`sessionState` additionally reflects an independent per-view
  archived flag `conn` itself can never produce or revert)
- `sessionHasEverConnected`: per-session now (not per-connection) — true once the CURRENT
  session's own subscription entry has received at least one successful poll response
- A `subscriptions` Map (sessionId -> `{cursor, hasEverSeenResponse}`), LRU-bounded to
  `MAX_SUBSCRIBED_SESSIONS` (5: current + 4 recently-viewed) — every entry is ACTIVELY
  polled (not just cached), so a recently-viewed-but-not-current session keeps receiving
  live events the whole time it's subscribed, replacing the old passive `sessionCursors` cache

**Key Actions**:
- `startUIPolling()`: starts the single shared long-poll loop against `POST /api/poll`,
  carrying the UI stream plus the current subscription set in one request body
- `connectSession(sessionId)`: subscribes a session (adds to the LRU, seeds its cursor,
  starts the shared loop if not already running)
- `sendMessage()`: POST to `/api/sessions/{id}/messages`
- `sendPermissionResponse()`, `interruptSession()`: REST calls
- Exponential backoff on error (up to 30 seconds), page-visibility pause/resume

### 5. Legion Store (`stores/legion.js`)
**Responsibility**: Multi-agent data (comms, minions)

**State**:
- `commsByLegion` (Map): Timeline communications per legion
- `minionsByLegion` (Map): Minions per legion

**Key Actions**:
- `loadTimeline()`: Paginated comm loading (100/page)
- `addComm()`: Real-time comm from WebSocket
- `sendComm()`: User sends comm to minion
- `createMinion()`

### 6. UI Store (`stores/ui.js`)
**Responsibility**: UI state (sidebar, modals, scroll, responsive)

**State**:
- `sidebarCollapsed`, `sidebarWidth`: Mobile-first sidebar
- `windowWidth`, `isMobile`: Responsive breakpoints
- `autoScrollEnabled`: Toggle message auto-scroll
- `activeModal`, `modalData`: Current modal

**Key Actions**:
- `toggleSidebar()`, `setSidebarWidth()`, `setAutoScroll()`
- `showModal()`, `hideModal()`, `showLoading()`, `hideLoading()`

### 7. Queue Store (`stores/queue.js`)
**Responsibility**: Per-session message queue state, pause/resume, item lifecycle

### 8. Schedule Store (`stores/schedule.js`)
**Responsibility**: Per-legion cron schedules, execution history, real-time updates

### 9. Resource Store (`stores/resource.js`)
**Responsibility**: Per-session resources (images/files), gallery state, full view modal, text content cache

### 10. Diff Store (`stores/diff.js`)
**Responsibility**: Per-session git diff data, view modes (total/commits), per-file diff cache

### 11. Task Store (`stores/task.js`)
**Responsibility**: Per-session SDK task tracking (TaskCreate/Update/List/Get tool integration)

### 12. MCP Store (`stores/mcp.js`)
**Responsibility**: Active MCP server state per session

### 13. MCP Config Store (`stores/mcpConfig.js`)
**Responsibility**: MCP server configuration CRUD (STDIO/SSE/HTTP, OAuth 2.1)

## Vue Components

For component documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#component-organization).

## Naming Conventions

- **camelCase**: Variables, functions, computed properties
- **PascalCase**: Component names
- **kebab-case**: Component file names

# Backend Architecture - Two Processes (Frontend API + Backend)

**Issue #498**: what used to be one process is now two. The **Frontend API**
(`src/`, `main.py`) serves the browser, authenticates it, and relays every
domain request — it holds zero session-execution code and zero domain state.
The **Backend** (`backend/`, `backend/main.py`) is the actual control plane:
SessionCoordinator, ClaudeSDK, Legion, and everything else that used to live
directly in the old unified `src/`. `shared/` holds the handful of
zero-SDK-dependency utilities both tiers import (`event_queue.py`,
`logging_config.py`, `exception_handlers.py`). A static test
(`src/tests/test_import_boundary.py`) enforces that `src/` can never import
from `backend/` — this is a structural guarantee, not a convention.

Single-user self-hosted use (the default) auto-starts Backend as a child
process of Frontend on an OS-assigned free port with a freshly generated
credential (`src/backend_supervisor.py`) — no manual configuration required.
`--remote-backend-url`/`--remote-backend-token` point Frontend at a
manually-run or genuinely remote Backend instead.

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Browser (Vue 3 Frontend)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Pinia Stores │  │ Components   │  │ Vue Router   │             │
│  │ (13 stores)  │  │  (99+ files) │  │  (routing)   │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└────────┬────────────────────┬────────────────────┬──────────────────┘
         │                    │                    │
         │ HTTP long-polling + REST API (browser token auth)         │
         │                    │                    │
┌────────▼────────────────────▼────────────────────▼──────────────────┐
│              Frontend API (src/web_server.py, main.py)               │
│  Static SPA serving · browser AuthMiddleware · zero domain state     │
│  ┌──────────────────┐ ┌──────────────────┐ ┌───────────────────┐   │
│  │ Generic relay     │ │ poll_relay.py     │ │ backend_supervisor │   │
│  │ (src/routers/     │ │ (fans Backend's   │ │ .py (auto-start,   │   │
│  │  relay.py)         │ │  poll streams out │ │  readiness gate,   │   │
│  │                    │ │  to local queues) │ │  crash/restart)    │   │
│  └──────────────────┘ └──────────────────┘ └───────────────────┘   │
└────────┬─────────────────────────────────────────────────────────────┘
         │ backend-scoped bearer token (never the browser's own token)
┌────────▼─────────────────────────────────────────────────────────────┐
│               Backend (backend/web_server.py, backend/main.py)       │
│  Own AuthMiddleware · own /health + /ready · single-tenant control   │
│  plane — "the actual meat of the platform"                           │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    SessionCoordinator                           │ │
│  │  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐   │ │
│  │  │SessionMgr  │  │ProjectMgr  │  │  ClaudeSDK            │   │ │
│  │  │(state)     │  │(hierarchy) │  │  (SDK wrapper)        │   │ │
│  │  └────────────┘  └────────────┘  └───────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    LegionSystem (Multi-Agent)                   │ │
│  │  ┌─────────────┐  ┌──────────┐  ┌────────────────────────┐   │ │
│  │  │ Legion      │  │Overseer  │  │  CommRouter            │   │ │
│  │  │ Coordinator │  │Control   │  │  (minion comms)        │   │ │
│  │  └─────────────┘  └──────────┘  └────────────────────────┘   │ │
│  │  ┌──────────────────────────────────────────────────────┐     │ │
│  │  │  MemoryManager (distillation, reinforcement)         │     │ │
│  │  └──────────────────────────────────────────────────────┘     │ │
│  └────────────────────────────────────────────────────────────────┘ │
└────────┬─────────────────────────────────────────────────────────────┘
         │ query() API
┌────────▼─────────────────────────────────────────────────────────────┐
│                   Claude Agent SDK (External Package)                 │
│              Streaming conversations, tool execution                  │
└───────────────────────────────────────────────────────────────────────┘
```

## Frontend API File Organization (`src/`, `shared/`)

### Entry Point
- **`main.py`**: Arg parsing, logging config, resolves Backend connection
  (manual `--remote-backend-url`/`--remote-backend-token`, or auto-start via
  `BackendSupervisor`), uvicorn server start

### `src/web_server.py` - **Frontend Shell**
**Main Class**: `ClaudeWebUI`

**Responsibilities**:
- Static SPA serving, browser-facing `AuthMiddleware` (issue #728)
- Owns `backend_client` (httpx relay wrapper), `poll_relay` (background
  long-poll fan-out), optionally `backend_supervisor` (auto-start lifecycle)
- Dynamic OAuth callback path mirroring for shared MCP servers
  (`resync_oauth_callback_paths()` — Backend usually isn't independently
  publicly reachable, so custom callback paths relay through Frontend)
- **Zero session-execution code.** No SessionCoordinator, no domain state.

### `src/routers/` - **Frontend-Side Routes Only**
- **`core.py`**: `/`, `/health`, `/ready`, `/api/auth/check`
- **`poll.py`**: `POST /api/poll` — one multiplexed request carrying the UI
  stream plus a subscription set of session streams; reads LOCAL EventQueues
  populated by `poll_relay.py`, not direct coordinator callbacks. Also the
  one-shot `GET /api/poll/cursor`/`GET /api/poll/session/{id}/cursor`
  bootstrap endpoints (not long-polling, not part of epic #1990's removal)
- **`config.py`**: `/api/config` — merged read (local `networking`/
  `backend_connection` + relayed Backend sections) / split write
- **`system.py`**: `POST /api/system/restart` only — pulls/switches Frontend's
  own repo and re-execs Frontend itself; in embedded mode also stops the
  current Backend child first so re-exec's normal startup auto-starts a fresh
  one from the same, already-pulled code (remote mode leaves Backend alone
  entirely). Backend's other `/api/system/*` routes (git-status, git-branches,
  git-commits, docker-status) stay relayed unchanged — in embedded mode
  they're the same repo either way. A blind relay of `restart` here (the
  behavior inherited by simply relocating the old unified router into
  `backend/routers/` in Phase 1) restarted Backend only, silently leaving
  Frontend running stale code forever — found in manual post-merge testing.
- **`relay.py`**: generic catch-all reverse-proxy for everything else under
  `/api/*`, plus the default `/oauth/callback` path. Registered last so the
  routers above take priority.

### `src/backend_client.py`, `src/backend_supervisor.py`, `src/poll_relay.py`, `src/frontend_config.py`
- **`backend_client.py`**: httpx wrapper — generic relay, JSON convenience
  methods, `/health`+`/ready` checks. Injects the backend-scoped token, never
  the browser's own.
- **`backend_supervisor.py`**: owns the auto-started local Backend subprocess
  — OS-assigned port, fresh token, readiness gating, crash/restart with
  backoff (capped, then marks degraded), clean SIGTERM-then-SIGKILL shutdown.
- **`poll_relay.py`**: one consolidated background long-poll task carrying a
  dynamic subscription set (UI plus every recently-active session) against
  Backend's own multiplexed `POST /api/poll`, fanning results into local
  `EventQueue`s.
- **`frontend_config.py`**: Frontend's own `networking`/`backend_connection`
  config sections — reads/writes only its own keys in the shared
  `~/.config/cc_webui/config.json`, leaving Backend's sections untouched.

### `shared/` - **Used By Both Tiers**
- **`event_queue.py`**: `EventQueue` — cursor-based async event buffer
- **`logging_config.py`**: category-based debug logging setup
- **`exception_handlers.py`**: `handle_exceptions` decorator for route handlers

## Backend File Organization (`backend/`)

### Entry Point
- **`backend/main.py`**: Arg parsing (own token, own debug flags), logging
  config, `configure_webui_base_url()` (Docker/LiteLLM sidecar callback
  advertising — issue #498 Phase 4), uvicorn server start. Independently
  runnable (`python -m backend.main`) for manual/remote deployment.

### `backend/web_server.py` - **Backend Control-Plane Application**
**Main Class**: `BackendApp`

**Responsibilities**:
- Owns `SessionCoordinator` and every session-execution/control-plane manager
- Own `AuthMiddleware` validating the backend-scoped bearer token
- Own `/health` (liveness) + `/ready` (readiness — true once
  `SessionCoordinator` and its managers finish constructing)
- Own `POST /api/poll` (`backend/routers/poll.py`) — the real EventQueues
  SessionCoordinator writes to, served through one multiplexed endpoint
- Own `/api/config` (`backend/routers/config.py`) — every AppConfig section
  except `networking`/`backend_connection`
- `/api/internal/oauth-callback-paths` — exposes the dynamic custom-OAuth-
  callback-path registry so Frontend can mirror it
- Permission callback creation with asyncio.Future for async approval
- Session state change broadcasting

#### `backend/session_coordinator.py` (~5700 lines) - **Central Orchestrator**
**Main Class**: `SessionCoordinator`

**Responsibilities**:
- Ties together SessionManager, ProjectManager, ClaudeSDK, DataStorage, MessageProcessor
- Manages active SDK instances per session
- Handles ExitPlanMode detection and auto-reset
- Permission update tracking
- Tool use tracking for orphaned detection

**Key Methods** (line numbers drift often — use `grep -n "async def "` for current locations):
- `create_session()`: Session + storage + SDK initialization
- `start_session()`: Start/resume SDK, send client_launched message
- `send_message()`: Queue message to SDK, update processing state
- `interrupt_session()`: Stop active SDK processing
- `set_permission_mode()`: Runtime permission mode changes
- `create_ephemeral_session()`: Scheduled/ephemeral session creation (bundles
  a `SessionConfig` — a #498-era bug had this passing flat kwargs instead,
  fixed with regression tests, see git history)
- `_create_message_callback()`: Process SDK messages, detect completion

#### `backend/session_manager.py` - **Session Lifecycle & State**
**Main Classes**: `SessionState` (enum), `SessionInfo` (dataclass), `SessionManager`

**SessionState Values**:
- `CREATED`, `STARTING`, `ACTIVE`, `PAUSED`, `TERMINATING`, `TERMINATED`, `ERROR`

**SessionInfo Fields** (Extended for Legion):
- Standard: `session_id`, `state`, `working_directory`, `current_permission_mode`, `tools`, `model`, `name`, `order`
- Legion: `role`, `is_overseer`, `parent_overseer_id`, `child_minion_ids`, `capabilities`, `initialization_context`

**Key Methods**:
- `create_session()`: Create directory, persist state.json
- `start_session()`, `pause_session()`, `terminate_session()`: State transitions
- `update_processing_state()`: Track active processing
- `update_permission_mode()`: Runtime mode changes

#### `backend/project_manager.py` - **Project Hierarchy & Organization**
**Main Classes**: `ProjectInfo` (dataclass), `ProjectManager`

**ProjectInfo Fields** (Extended for Legion):
- Standard: `project_id`, `name`, `working_directory`, `session_ids`, `is_expanded`, `order`
- Legion: `is_multi_agent`, `minion_ids`, `max_concurrent_minions`, `active_minion_count`

**Key Methods**:
- `create_project()`: Create with order shifting
- `add_session_to_project()`, `remove_session_from_project()`
- `reorder_projects()`, `reorder_project_sessions()`

#### `backend/claude_sdk.py` - **SDK Wrapper & Message Queue**
**Main Class**: `ClaudeSDK`

**Responsibilities**:
- Wraps Claude Agent SDK with async queue
- Manages conversation loop
- Handles streaming responses
- MCP server integration for Legion tools

**Key Components**:
- `_message_queue`: Async queue for user messages
- `_conversation_task`: Background processing loop
- `_sdk_client`: ClaudeSDKClient instance
- `_mcp_server`: Optional MCP server for Legion tools

**Key Methods**:
- `start()`: Initialize SDK client, start conversation loop
- `send_message()`: Enqueue message
- `interrupt_session()`: Set interrupt flag
- `set_permission_mode()`: Send mode change to SDK
- `_conversation_loop()`: Process queue, stream responses

#### `backend/message_parser.py` - **Message Normalization**
**Main Classes**: `MessageType` (enum), `ParsedMessage` (dataclass), `MessageProcessor`

**Responsibilities**:
- Convert between SDK objects, storage format, and event-queue format
- Unified processing for consistency

**Handlers**: `SystemMessageHandler`, `AssistantMessageHandler`, `UserMessageHandler`, `ResultMessageHandler`, `PermissionRequestHandler`, `PermissionResponseHandler`

**MessageProcessor Methods**:
- `process_message()`: Raw SDK message → ParsedMessage, consulted for its re-derived
  `ParsedMessage` by `SessionCoordinator._compute_display_metadata_for_storage()` and
  `_create_message_callback()` (latest-message tracking, permission-mode detection, the
  Task registry feed, unread-state, analytics)

#### `backend/data_storage.py` - **Persistent Storage**
**Main Class**: `DataStorageManager`

**Files Managed**:
- `messages.jsonl`: Append-only message log
- `state.json`: Session metadata (managed by SessionManager)

**Key Methods**:
- `append_message()`, `read_messages()`, `get_message_count()`, `cleanup()`

#### Additional Backend Modules (`backend/`)

- **`application_service.py`**: Top-level service facade used by `web_server.py`
- **`config_manager.py`**: `~/.config/cc_webui/config.json` read/write —
  every `AppConfig` section except `networking`/`backend_connection`
  (Frontend-owned, see `src/frontend_config.py`). `save_config()` merges
  into the existing file rather than overwriting it, so it doesn't clobber
  Frontend-owned keys it doesn't know about.
- **`docker_utils.py`**: Docker image/mount configuration helpers for per-session isolation
- **`file_upload.py`**: Multipart file upload handling and session attachment storage
- **`history_distiller.py`**: Session history summarization for archival context continuity
- **`history_search.py`** / **`search_scan.py`**: Whole-history substring search (issue #2030 stage 3): `history_search.py` holds the query matcher / `findMatches`-parity matching / search cursors, `FileMessageHistoryReader.search` the backward scan (behind the reader seam), `search_scan.py` the separate search pool with single-flight, per-(session, client_id) supersede, a per-session flight cap, disconnect cancel and waiter cap (routes `GET .../search`)
- **`message_history_reader.py`**: the `MessageHistoryReader` seam over `messages.jsonl` — `read_tail`/`read_before` (stage 1) plus stage 3 PR 3A `read_around`/`read_after`/`resolve_message_id` (`GET .../messages?around=|around_id=|after=`; `resolve_message_id` is the one O(file) step and runs on the cold-scan pool under kind `resolve:*`, see `SessionQuery.resolve_message_id`; the reader docstring has the DB implementation sketch)
- **`legion_system.py`**: Top-level Legion system wiring (coordinator + overseer + comm router)
- **`mcp_config_manager.py`**: Per-session MCP server configuration persistence (STDIO/SSE/HTTP, OAuth)
- **`mock_sdk.py`**: `MockClaudeSDK` — fixture replay for testing without live SDK calls
- **`oauth_manager.py`**: OAuth 2.1 token acquisition and storage
- **`oauth_refresh_manager.py`**: Background OAuth token refresh loop
- **`permission_service.py`**: Permission evaluation and decision routing
- **`session_config.py`**: Session configuration dataclass combining all per-session options;
  also home of `validate_and_normalize_working_directory()` (relocated here from the
  old unified `web_server.py`, since its only real callers are backend-side)
- **`task_utils.py`**: SDK task reconstruction helpers used by `task.js` store sync
- **`template_variables.py`**: Template variable substitution (e.g., `{{session_id}}`)
- **`timestamp_utils.py`**: Consistent timestamp parsing/formatting across storage and API

## Legion Multi-Agent System

### Legion Components (`backend/legion/`)

#### `backend/legion/legion_coordinator.py` - **Legion Lifecycle Management**
**Main Class**: `LegionCoordinator`

**Responsibilities**:
- Legion creation and deletion
- Fleet control (halt all, resume all, emergency halt)
- Central capability registry (MVP: keyword search)

#### `backend/legion/overseer_controller.py` - **Minion Management**
**Main Class**: `OverseerController`

**Responsibilities**:
- Minion lifecycle: create_minion_for_user(), spawn_minion(), dispose_minion()
- Enforce parent authority (only parent can dispose children)
- Memory transfer on disposal
- Capability registration

#### `backend/legion/comm_router.py` - **Inter-Agent Communication**
**Main Class**: `CommRouter`

**Responsibilities**:
- Convert between Comms and SDK Messages
- Route Comms to minions or user
- Handle interrupt priorities (HALT, PIVOT)
- Broadcast persisted Comms to WebSocket/UI notification/audit writer
- Parse and validate #tag references

#### `backend/legion/memory_manager.py` - **Memory & Learning** (Planned)
**Main Class**: `MemoryManager`

**Responsibilities** (Future):
- Distill task completions into structured memories
- Reinforce memories based on outcome feedback
- Promote high-quality memories to long-term
- Transfer knowledge between minions
- Support minion forking with memory copy

#### `backend/legion/legion_mcp_tools.py` - **MCP Tools for Minions**
**Main Class**: `LegionMCPTools`

**Tools Provided**:
- **Communication**: `send_comm`
- **Lifecycle**: `spawn_minion`, `dispose_minion`
- **Discovery**: `list_minions`, `get_minion_info`, `search_capability`

**Integration**: Single instance per legion, attached to all minion SDK sessions

### Legion Data Models (`backend/models/legion_models.py`)

**Core Entities**:
- `Comm`: High-level message with routing info
- `CommType` enum: TASK, QUESTION, REPORT, INFO, HALT, PIVOT, THOUGHT, SPAWN, DISPOSE, SYSTEM
- `MemoryEntry`, `MinionMemory`: Knowledge management (future)

## Additional Backend Systems

### Queue System (`backend/queue_manager.py`, `backend/queue_processor.py`)

FIFO message queue with JSONL persistence for timed/sequential message delivery.

- **QueueManager**: State management with `QueueItem` dataclass (queue_id, session_id, content, reset_session, status, position). Storage via `queue.jsonl` with event replay on startup.
- **QueueProcessor**: Background asyncio task delivering queued messages with timing guards (`min_wait_seconds=10`, `min_idle_seconds=10`). Auto-starts sessions, handles pausing, polls `is_processing` without timeout.

### Cron Scheduler (`backend/legion/scheduler_service.py`)

Background service evaluating cron schedules every 30 seconds.

- **SchedulerService**: Creates/manages `Schedule` objects with croniter evaluation. Enqueues prompts via SessionCoordinator when due. Records `ScheduleExecution` history to JSONL. Auto-cancels on minion disposal.
- **Models**: `Schedule` (cron, next_run, status, failure tracking), `ScheduleExecution` (execution record), `ScheduleStatus` (ACTIVE/PAUSED/CANCELLED)

### Archive Manager (`backend/legion/archive_manager.py`)

Timestamped archival of minion session data before disposal.

- **ArchiveManager**: Copies messages.jsonl, state.json, and disposal metadata to `data/archives/minions/{minion_id}/{timestamp}/`. Returns `ArchiveResult` with archive path and file count.

### Permission Resolver (`backend/permission_resolver.py`)

Multi-source permission merge for effective permission preview.

- **`resolve_effective_permissions()`**: Parses permissions from user/project/local settings files and session-level allowed_tools. Returns list of `{permission, sources}` with source tracking.

### Resource MCP Tools (`backend/mcp/resource_mcp_tools.py`)

Session-scoped MCP server for agent resource display in the task panel.

- **ResourceMCPTools**: Creates per-session MCP servers with `register_resource` and `register_image` (deprecated alias) tools. Validates file path, extension, size (10MB max, 100 per session). Broadcasts `resource_registered` via WebSocket.

### Template Manager (`backend/template_manager.py`)

File-based minion template CRUD with slug naming.

- **TemplateManager**: Stores templates as JSON+MD file pairs in `data/templates/`. Supports slug-based filenames for human readability. Seeds default templates from `backend/default_templates/`. Migrates legacy UUID filenames on load.

### Skill Manager (`backend/skill_manager.py`)

Global skill deployment and symlink management.

- **SkillManager**: Syncs skills from `backend/default_skills/` to `~/.cc_webui/skills/`, creates symlinks in `~/.claude/skills/`. Detects conflicts with user files. Returns (added, updated, removed) counts.

## Data Directory Structure

Both tiers share one `data/` tree (same `--data-dir` value passed through to
an auto-started Backend by `backend_supervisor.py`). Frontend itself stores
no domain data — only its own logs.

```
data/
├── logs/                           # Frontend API's own logs
│   ├── coordinator.log             # (legacy name — pre-#498 unified process)
│   ├── error.log                   # All errors
│   ├── parser.log                  # Message parsing
│   ├── sdk_debug.log               # SDK integration
│   ├── storage.log                 # File operations
│   ├── polling.log                 # Poll transport signal logging
│   ├── frontend.log                # Raw combined capture of Frontend's own stdout/stderr
│   │                                # (bypass content + supervisor lifecycle events), rotated;
│   │                                # installed by src/frontend_log_tee.py (issue #2027)
│   └── backend/                    # Backend subprocess's own logs (issue #498)
│       └── backend.log             # Backend's stdout/stderr, piped and rotated by
│                                    # backend_supervisor.py (issue #2027)
│
├── projects/{uuid}/                # One folder per project
│   └── state.json                  # ProjectInfo serialized
│
├── sessions/{uuid}/                # One folder per session
│   ├── state.json                  # SessionInfo serialized
│   ├── messages.jsonl              # Append-only message log
│   ├── queue.jsonl                 # Message queue event log
│   └── resources/                  # Registered resources (images/files)
│
├── templates/                      # Minion templates (JSON + MD pairs)
│   ├── {slug}.json                 # Template configuration
│   └── {slug}.md                   # Template system prompt
│
├── archives/                       # Archived minion data (post-disposal)
│   └── minions/{minion_id}/{ts}/   # Timestamped snapshots
│
└── legions/{uuid}/                 # One folder per legion (multi-agent project)
    ├── schedules.json              # Cron schedule definitions
    ├── schedule_history.jsonl      # Schedule execution log
    └── minions/{minion_id}/
        ├── minion_state.json
        ├── session_messages.jsonl  # SDK messages
        ├── short_term_memory.json  # (Future)
        └── long_term_memory.json   # (Future)
```

**Issue #2027**: `backend.log` and `frontend.log` are both size-capped and
backup-bounded (`shared/raw_log_rotator.py::RotatingRawLogWriter` — the
`RotatingFileHandler` equivalent for raw byte streams, not Python
`LogRecord`s), so neither grows unbounded the way `backend.log` used to.
Category log files (`sdk_debug.log`, `storage.log`, etc.) no longer echo
their own DEBUG/INFO content to the console/combined logs — only ERROR+
still reaches both the console and `error.log` for every category, exactly
as before. `src.backend_supervisor`'s own lifecycle logs (spawn, unexpected
exit with code/signal, restart scheduled, degraded, stopped) reach the
console unconditionally, independent of any `--debug-*` flag, and are
therefore captured into `frontend.log` by the tee.

## API Endpoint Reference

For the complete endpoint reference with request/response details, see [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md).

**Summary**: 50+ REST endpoints across 10 domains (projects, sessions, files, resources, diffs, queue, legion, schedules, templates, system), owned by `backend/routers/` and reached through Frontend's generic relay (`src/routers/relay.py`) — plus `core.py`/`poll.py`/`config.py`/`system.py`'s handful of Frontend-side routes. One multiplexed HTTP long-polling endpoint (`POST /api/poll`) exists on **both** tiers post-#498/epic #1990 stage 5c — Backend's is the real one SessionCoordinator writes to; Frontend's reads from local EventQueues kept in sync by `poll_relay.py`. The browser only ever talks to Frontend's copy.

## Message Flow Architecture

### SDK Message → Storage → Poll-Relay → Long-Poll Event Flow

```
1. ClaudeSDK (backend/) receives message from claude_agent_sdk
   ↓
2. ClaudeSDK builds the one canonical MessageRecord via MessageRecord.from_sdk_message()
   (issue #2084 stage 3-B) — reused as-is for both the storage write and the live callback,
   no separate MessageProcessor.process_message()/prepare_for_storage() conversion step
   ↓
3. display_hook (SessionCoordinator._compute_display_metadata_for_storage) computes display
   metadata once and stamps it onto the record before it's persisted or delivered
   ↓
4. SessionCoordinator stores record.to_dict() via DataStorageManager.append_message()
   └─ Writes to messages.jsonl
   ↓
5. ClaudeSDK invokes message_callback with that same record.to_dict() — in production this
   is SessionCoordinator._create_message_callback (backend/session_coordinator.py:4747), the
   method actually registered as ClaudeSDK's message_callback. BackendApp._create_message_callback
   (backend/web_server.py:833) is a separate, differently-behaving method: it's registered as a
   downstream subscriber callback that SessionCoordinator's own callback forwards to, not the
   primary SDK callback itself.
   ↓
6. SessionCoordinator pushes event to session EventQueue (Backend's own, real one)
   └─ EventQueue.put() wakes any waiting poll request — served by
      backend/routers/poll.py
   ↓
7. Frontend's poll_relay.py background task (long-polling Backend's poll
   endpoint) receives the event, re-appends it to Frontend's LOCAL EventQueue
   ↓
8. Frontend's own poll.py (src/routers/poll.py) serves it from that local
   queue to the browser's long-poll request — nested under this session's
   entry in the multiplexed response's {events, next_cursor, ...} per-stream
   shape (epic #1990 stage 5c), same per-stream fields as before the #498 split
   ↓
9. Frontend poll loop (browser) receives it, updates Pinia stores reactively
```

### User Message → SDK Flow

```
1. User types in frontend, clicks Send
   ↓
2. Frontend POST /api/sessions/{id}/messages {message: "..."}
   ↓
3. src/routers/relay.py's generic catch-all forwards method/path/query/body/
   headers (browser token stripped, backend-scoped token injected) to Backend
   ↓
4. backend/routers/sessions.py REST handler receives the relayed request
   ↓
5. Calls SessionCoordinator.send_message(session_id, content)
   ↓
6. SessionCoordinator marks session.is_processing = True
   ↓
7. ClaudeSDK.send_message() enqueues to _message_queue
   ↓
8. ClaudeSDK._conversation_loop() picks up from queue
   ↓
9. Sends to claude_agent_sdk via query(prompt=message)
   ↓
10. SDK streams back responses (loop back to SDK Message flow above)
   ↓
11. On ResultMessage, SessionCoordinator sets is_processing = False
```

### Permission Flow

```
1. SDK needs permission for tool (e.g., Edit, Write) — inside Backend
   ↓
2. SDK calls permission_callback() from backend/web_server.py
   ↓
3. backend/web_server.py stores permission request message (with suggestions if any)
   ↓
4. backend/web_server.py pushes permission_request event to session EventQueue
   ↓
5. Frontend's poll_relay.py picks it up (same relay chain as any other event) and
   fans it into Frontend's local queue; browser's poll loop displays the
   permission modal with suggestions
   ↓
6. User clicks Allow/Deny (optionally applies suggestions)
   ↓
7. Frontend POST /api/sessions/{id}/permission/{request_id} — relayed via
   src/routers/relay.py (this route lives in backend/routers/session_runtime.py
   now, relocated from the old unified core.py since it acts on backend-owned
   state: PermissionService.pending_permissions)
   ↓
8. backend/web_server.py resolves asyncio.Future with user's decision
   ↓
9. Permission callback returns {behavior: "allow"/"deny", updated_permissions: [...]}
   ↓
10. SDK receives response and continues/aborts tool execution
```

## Common Development Scenarios

### Finding Where Functionality Lives

**Problem**: Need to change how Edit tool is displayed
→ **Solution**: `frontend/src/components/messages/tools/EditToolHandler.vue`

**Problem**: Session not starting, need to debug SDK initialization
→ **Solution**: `backend/session_coordinator.py`'s `start_session()` + enable `--debug-sdk` on Backend

**Problem**: Long-poll events not arriving
→ **Solution**: Check `backend/routers/poll.py` (the real EventQueues) and
`src/poll_relay.py` (the fan-out mechanism) and `src/routers/poll.py` (what the
browser actually talks to); enable `--debug-polling`

**Problem**: Messages not persisting
→ **Solution**: `backend/data_storage.py`'s `append_message()` + enable `--debug-storage` on Backend

**Problem**: Permission callback not triggering
→ **Solution**: `backend/web_server.py`'s `_create_permission_callback()` + enable `--debug-permissions` on Backend

**Problem**: Add new REST endpoint
→ **Solution**: Add route in the appropriate `backend/routers/*.py` file, register it
in `backend/routers/__init__.py`'s `register_all()`. Frontend's generic relay
(`src/routers/relay.py`) picks it up automatically — no Frontend-side change needed
unless the route needs special-casing (like `/api/config` or the poll endpoints).

**Problem**: Frontend<->Backend relay not working
→ **Solution**: Check `src/backend_client.py` (the relay client), confirm Backend
is actually reachable (`curl <backend_url>/health`), check
`src/backend_supervisor.py`'s logs (`data/logs/backend/backend.log`) if auto-started

**Problem**: Add new Vue component
→ **Solution**: Create `.vue` file, register in parent component or router

**Problem**: Add new tool handler
→ **Solution**: See TOOL_HANDLERS.md for Vue 3 component creation guide

### Understanding State Management

**Session States** (`SessionState` enum):
- `CREATED`: Session exists but SDK not started
- `STARTING`: Transitioning to active
- `ACTIVE`: SDK running, can send/receive messages
- `PAUSED`: Awaiting user input (permissions)
- `TERMINATED`: SDK stopped cleanly
- `ERROR`: Startup or runtime failure (check `error_message` field)

**Processing State** (`is_processing` boolean):
- `True`: Session actively processing user input (disable send button)
- `False`: Session idle, ready for new input
- Automatically reset on `ResultMessage` or errors

**Permission Modes**:
- `default`: Prompt for everything not pre-approved in .claude/settings
- `acceptEdits`: Auto-approve Edit/Write/etc (permissive)
- `plan`: Planning mode (auto-resets to default after ExitPlanMode)
- `bypassPermissions`: No prompts at all

## Component Dependencies

```
main.py (Frontend API)
  └─ src/web_server.py (ClaudeWebUI)
      ├─ src/backend_client.py       — relay to Backend
      ├─ src/backend_supervisor.py   — auto-start Backend (if no --remote-backend-url)
      ├─ src/poll_relay.py           — fan Backend's poll streams into local queues
      ├─ src/frontend_config.py      — networking/backend_connection config sections
      └─ shared/ (logging_config.py, exception_handlers.py, event_queue.py)

backend/main.py (Backend)
  └─ backend/web_server.py (BackendApp)
      ├─ backend/session_coordinator.py
      │   ├─ backend/session_manager.py
      │   ├─ backend/project_manager.py
      │   ├─ backend/claude_sdk.py
      │   │   ├─ backend/data_storage.py
      │   │   ├─ backend/message_parser.py
      │   │   └─ shared/logging_config.py
      │   ├─ backend/data_storage.py
      │   ├─ backend/message_parser.py
      │   ├─ backend/queue_manager.py
      │   ├─ backend/queue_processor.py
      │   ├─ backend/template_manager.py
      │   ├─ backend/skill_manager.py
      │   ├─ backend/permission_resolver.py
      │   ├─ backend/mcp/resource_mcp_tools.py
      │   ├─ backend/legion/legion_coordinator.py (if multi-agent)
      │   │   ├─ backend/legion/overseer_controller.py
      │   │   ├─ backend/legion/comm_router.py
      │   │   ├─ backend/legion/memory_manager.py
      │   │   ├─ backend/legion/legion_mcp_tools.py
      │   │   ├─ backend/legion/scheduler_service.py
      │   │   └─ backend/legion/archive_manager.py
      │   └─ shared/logging_config.py
      ├─ backend/message_parser.py
      └─ shared/logging_config.py
```

## Testing & Development Patterns

### Standard Testing Configuration

**CRITICAL**: Always use port 8001 (Frontend API) for testing to avoid
conflicts with production on port 8000. Backend's port is allocated
dynamically — do not hardcode a second fixed port (see
`.claude/skills/custom-environment-setup/SKILL.md` for the full explanation
of why, tied to issue #1825).

```bash
# Test run — auto-starts Backend, no second port to manage
uv run python main.py --host 0.0.0.0 --debug-all --data-dir test_data --port 8001

# Production run
uv run python main.py --port 8000
```

### Process Management - REQUIRED PATTERN

**CRITICAL**: Always kill processes by PID, never by name. Killing the
Frontend API gracefully (SIGTERM, not `-9`) cascades to its auto-started
Backend child automatically (`src/backend_supervisor.py`'s shutdown
handling) — you should not need to separately find and kill Backend's PID
in the normal case. If you do need Backend's PID directly (e.g. after a
force-kill of Frontend), find it by process name since its port isn't
predictable: `ps aux | grep "backend.main" | grep -v grep`.

**Windows**:
```bash
# Find process
netstat -ano | findstr ":8001"

# Kill by PID
taskkill /PID <PID> /F
```

**Unix/Linux/macOS**:
```bash
# Find process
lsof -i :8001

# Kill by PID
kill <PID>
# or: kill -9 <PID>  — if you must, then also check for an orphaned Backend
# process (ps aux | grep backend.main) since -9 skips the graceful cascade
```

### Running Tests

```bash
# Iterative/dev default — both suites, slow + regenerate_fixture tests deselected
# by pyproject.toml's addopts (the split between backend/tests and src/tests is
# real, they cover different processes)
uv run pytest backend/tests/ src/tests/ -v

# Specific test file
uv run pytest backend/tests/test_session_manager.py -v
uv run pytest src/tests/test_backend_supervisor.py -v

# With coverage
uv run pytest backend/tests/ src/tests/ --cov=backend --cov=src --cov-report=html

# Full gate — REQUIRED before PR creation: runs every test including all
# slow-marked ones (only regenerate_fixture is excluded, since it writes to a
# committed fixture and must be invoked explicitly). Verified via --collect-only
# to select 3545/3546 tests. This is what guarantees the mandatory live
# two-process E2E test (issue #498's hard acceptance gate,
# src/tests/test_live_two_process_e2e.py — now slow-marked for its real
# subprocess-boot cost) still runs before every merge.
uv run pytest backend/tests/ src/tests/ shared/tests/ -m "not regenerate_fixture" -v
```

**Marking a test `@pytest.mark.slow`**: do this when a test boots a real
subprocess (`subprocess.Popen`/`asyncio.create_subprocess_exec` spawning a
`backend.main`/`src` entrypoint or an equivalent real process), or deliberately
simulates a multi-second timeout/retry/hang path — **and** real
`pytest --durations=0` data shows it at roughly 3s or more. Name alone is not
sufficient justification: several suggestively-named files (e.g.
`test_docker_utils_ssh.py`, `test_ssh_key_secret.py`,
`test_issue_1825_litellm_port.py`, `test_issue_1789_oauth_custom_callback.py`)
were measured at under 1.5s and are correctly left unmarked — confirm with real
numbers before adding the marker.

### Frontend Development

```bash
# Terminal 1: Frontend API (auto-starts Backend as a child process)
uv run python main.py --host 0.0.0.0 --port 8001 --debug-all

# Terminal 2: Frontend dev server
cd frontend
VITE_BACKEND_PORT=8001 npm run dev  # http://localhost:5173 — proxies to the Frontend API port, not Backend's
```

## Key Architectural Decisions

**Why SessionCoordinator?**
- Central orchestrator pattern prevents circular dependencies
- Single point of control for SDK lifecycle
- Coordinates state across multiple managers

**Why MessageProcessor?**
- SDK message format ≠ storage format ≠ WebSocket format
- Unified processing ensures consistency
- Handles backward compatibility as SDK evolves

**Why Separate Project and Session Managers?**
- Projects = lightweight grouping (working_directory + sessions)
- Sessions = heavy (SDK instances, message history, state)
- Enables multi-project session support (future)

**Why JSONL for messages?**
- Append-only is safe for concurrent access
- Line-by-line reading enables pagination
- Easy to repair if corruption occurs
- Human-readable for debugging

**Why asyncio.Future for permissions?**
- SDK permission callback is synchronous from its perspective
- HTTP long-polling response is asynchronous
- Future bridges sync callback ↔ async REST response

**Why Vue 3 + Pinia?**
- Reactive state management eliminates manual UI updates
- Component architecture improves maintainability
- Vue DevTools provides powerful debugging
- TypeScript support improves code quality
- Hot Module Replacement accelerates development

**Why MCP Tools for Legion?**
- Explicit intent (no ambiguity in minion actions)
- Structured parameters (type-safe, validated)
- Clear error feedback (minions can act on specific errors)
- Debuggable (tool calls visible in session messages)
- Self-documenting (tool descriptions teach minions usage)

**Why split Frontend API and Backend into two processes? (issue #498)**
- A prior attempt at this split (#498/#499, reverted) used an in-process
  LOCAL/REMOTE mode flag on one shared codebase — nothing physically prevented
  a code path from quietly reaching session-execution internals in "frontend
  mode," and gaps (config mutation, watchdog visibility, Legion topology) kept
  surfacing because partial correctness was shippable
- Import-enforced structural separation (`src/tests/test_import_boundary.py`)
  makes "zero in-process execution in the Frontend API" provable by
  construction instead of relying on code-review vigilance
- Single-user self-hosted use isn't a special case — it's the same relay
  mechanism with Backend auto-started locally instead of pointed at manually,
  so there's exactly one code path to get right, not two

## Future Enhancements

**Frontend**:
- Syntax highlighting for code blocks (highlight.js/Prism)
- Virtual scrolling for large message lists
- Advanced filtering and search
- Multi-user authentication
- Theme customization

**Backend**:
- PostgreSQL for multi-instance deployments
- Redis for session state caching
- Prometheus metrics exporter
- Rate limiting and abuse prevention
- Multi-user authorization

**Legion**:
- Memory distillation and reinforcement
- Knowledge transfer on disposal
- Minion forking for A/B testing
- Enhanced capability discovery

## Additional Resources

- **User Guide**: [run_guide.md](./run_guide.md) - Setup, usage, troubleshooting
- **Frontend Architecture**: [frontend/CLAUDE.md](./frontend/CLAUDE.md) - Vue 3 stores, components, composables
- **API Reference**: [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md) - All REST + long-polling endpoints
- **Tool Handlers**: [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) - Vue 3 tool handler development
- **Legion Proposal**: [legion_proposal/LEGION_PROPOSAL.md](./legion_proposal/LEGION_PROPOSAL.md) - Multi-agent design
- **MCP Tools**: [legion_proposal/MCP_TOOLS_ARCHITECTURE.md](./legion_proposal/MCP_TOOLS_ARCHITECTURE.md) - Inter-agent communication
- **Development Plan**: [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) - Project roadmap
- **Claude Agent SDK**: https://github.com/anthropics/claude-agent-sdk

## Summary

Claude WebUI is a **production-ready web interface** for Claude Agent SDK with:

**Single-Agent Features**:
- Real-time streaming conversations with rich tool visualization (22 tool handlers)
- Project/session hierarchy with drag-and-drop reordering
- Four permission modes with smart suggestions and permission preview
- Message queue with timed delivery and auto-start
- Resource gallery (images and files from agents)
- Git diff viewer with per-commit and aggregate views
- Orphaned tool detection and cleanup
- Persistent message storage (JSONL + JSON)
- Vue 3 + Pinia reactive UI (13 stores, 99+ components)
- Mobile-responsive design

**Multi-Agent Features (Legion)**:
- Minion creation and management with templates
- Inter-agent communication (structured Comms)
- Cron-based scheduling for recurring agent tasks
- Timeline view for observability
- MCP tools for explicit minion actions (send_comm, spawn/dispose, discovery)
- Sandbox mode for minions
- Session archival on disposal

**Developer Experience**:
- Comprehensive debugging tools (per-category logs)
- 50+ REST endpoints + HTTP long-polling event streams
- Extensible architecture (22 tool handlers, easy to add more)
- Hot Module Replacement for instant feedback
- Vue DevTools integration

---

**important-instruction-reminders**

Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.
