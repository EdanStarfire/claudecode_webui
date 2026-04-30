## Implementation Plan for Issue #1230

**Title:** feat: unify template/session config storage with reset-icon UI and alphabetized state

**Approved decisions** (from WebUI-Agent):
- Q1: **Store explicit values even when equal to default** — user intent wins; consistent with profile pattern
- Q2: **One combined PR** — backend + frontend on a single branch, atomic merge
- Q3: **Pre-migration backup** — yes, one-time copy to `.legacy_pre_1230/<ts>/` with idempotent skip
- Q4: **Delete `_config_from_session_info()`** — migration is mandatory; orphan-template falls through `session.config > defaults`

---

### User Stories

**As a template author**, I want each field to clearly show whether its value is inherited (from profile or default) or explicitly set on the template, so I can predict what sessions will see at runtime.

**As a template author**, I want a reset control on every explicitly-set field that reverts it to the inherited value, so I can drop a customization without recreating the template.

**As a session user**, I want the same set/inherit/reset pattern in the session edit modal, applied over the resolved template/profile/default chain, so I can reason about my session's effective configuration.

**As a developer**, I want a single storage shape (`config: dict`) across templates, sessions, and profiles, so adding a new CONFIG_FIELD requires no per-tier plumbing changes.

**As a debugging operator**, I want session, template, and profile JSON files written with alphabetized keys, so diffs and inspection are predictable.

---

### Technical Approach

**Three-tier resolution chain (final):**

```
session.config > template.config > profile.config > field defaults
```

One storage shape per tier (`config: dict`), one UI pattern (reset icon + source label).

**Storage invariants after this change:**
- A field is "set on layer X" iff it appears as a key in layer X's `config` dict.
- Empty list `[]` is a valid stored value, distinct from "not set".
- An explicit value equal to the field default is still stored (per Q1).
- Orphaned keys (fields removed from CONFIG_FIELDS in a future release) are filtered at resolution time, not at load — never causes load failure.

**Removed concepts:**
- `MinionTemplate.template_overrides` (dict, currently unreachable from UI)
- `SessionInfo.session_overrides` (dict, replaced by `session.config`)
- All flat CONFIG_FIELD attributes on `MinionTemplate` and `SessionInfo`
- `skip_flat` plumbing in `session_manager.create_session`
- `_init_session_overrides()` and `_track_overrides()` smart-diff helpers
- `_config_from_session_info()` legacy resolution path
- Frontend's `toPayload` empty-list-to-null transforms (`[]` is now meaningful end-to-end; #1229 stopgap becomes redundant)
- Frontend's 4-state field-state model (`normal`/`profile`/`autofilled`/`modified`)

**Preserved concepts:**
- Identity/lifecycle fields stay flat: `template_id`, `name`, `role`, `description`, `capabilities`, `profile_ids`, `created_at`, `updated_at` on templates; `session_id`, `name`, `order`, `state`, `working_directory`, `current_permission_mode` (live runtime state — distinct from `config["permission_mode"]`), `is_processing`, `template_id`, project/legion fields on sessions
- `system_prompt` companion `.md` file pattern for templates — at load time, the `.md` content overlays `template.config["system_prompt"]`; at save time the `.md` is rewritten when `config["system_prompt"]` changes
- Profile area-scoped field validation (a `model`-area profile may only contain `model`/`thinking_mode`/`thinking_budget_tokens`/`effort` keys in its `config`) — unchanged
- `current_permission_mode` flat field on SessionInfo as live-runtime state (auto-resets after ExitPlanMode)

---

### Files to Modify

#### Backend

| File | Purpose |
|---|---|
| `src/models/minion_template.py` | Replace flat CONFIG_FIELDS + `template_overrides` with `config: dict`; rewrite `to_dict`/`from_dict` |
| `src/session_manager.py` | Replace flat CONFIG_FIELDS + `session_overrides` on SessionInfo with `config: dict`; rewrite `to_dict`/`from_dict`; rewrite `create_session` (drop `skip_flat`); add session-load migration |
| `src/template_manager.py` | Add template-load migration; route all writes through alphabetized JSON helper |
| `src/profile_manager.py` | Route writes through alphabetized JSON helper (no schema change) |
| `src/config_resolution.py` | Rewrite `resolve_effective_config()` and `resolve_template_config()` for new chain; delete `_config_from_session_info()`; keep `_LIST_FIELDS`/`_coerce_list()` for profile string-to-list normalization |
| `src/session_coordinator.py` | Drop `_init_session_overrides()`, `_track_overrides()`; route session-config updates into `session.config` dict; audit start_session for direct flat-field reads on `session_info` and replace with effective_config |
| `src/web_server.py` | API request/response shape for templates (POST/PUT `/api/templates*`) and sessions (POST/PATCH `/api/sessions*`) — accept/return `config` dict; remove `template_overrides`/`session_overrides` from request schemas |
| `src/storage_utils.py` (new, ~30 lines) | `write_alphabetized_json(path, data)` helper used by template_manager/session_manager/profile_manager |
| `src/proxy_addon.py` | Audit reads of `session_info.docker_proxy_allowlist_domains`/`assigned_secrets` — must read effective_config |
| `src/secret_injection.py` | Audit reads of `session_info.assigned_secrets` — must read effective_config |
| `src/permission_resolver.py` | Audit reads of `session_info.allowed_tools`/`disallowed_tools`/`setting_sources` — must read effective_config |
| `src/claude_sdk.py` | Audit any direct flat-field reads on session_info (start path likely already uses SessionConfig) |
| `src/session_config.py` | No change to CONFIG_FIELDS set; potentially expose a `DEFAULTS` dict helper for migration use |
| `src/tests/test_config_resolution.py` | Rewrite tests for new resolution chain; preserve replace-semantics tests from #1226 |
| `src/tests/test_template_manager.py` | New migration tests |
| `src/tests/test_session_manager.py` | New migration tests; update create_session expectations |
| `src/tests/test_session_coordinator.py` | Update for dropped helpers |

#### Frontend

| File | Purpose |
|---|---|
| `frontend/src/components/configuration/ConfigurationModal.vue` | Replace 4-state field model with `{effective_value, source, source_label, is_set_here, current_layer_value}`; rewrite `populateFormFromTemplate`/`populateFormFromSession`/`extractPayload`; drop `toPayload` empty-list-to-null transforms |
| `frontend/src/components/configuration/fields/FieldRenderer.vue` | Replace P/`<`/`*` badges with source label + reset icon; emit reset event |
| `frontend/src/components/configuration/AdvancedSettingsPanel.vue` | Pass through new field-state shape |
| `frontend/src/components/configuration/QuickSettingsPanel.vue` | Same |
| `frontend/src/composables/useFieldState.js` (new) | Compute `fieldsState` map by walking the chain (defaults → profile → template → session.config or template.config) |
| `frontend/src/stores/profile.js` | No change (already exposes profiles by area) |
| `frontend/src/stores/session.js` | Adjust `patchSession` payload shape: `{name?, config: {...}}` (no flat fields, no session_overrides) |
| `frontend/dist/` | Rebuild via `npm run build`; commit |

---

### Implementation Steps

#### Phase 1 — Backend Data Model (atomic, breaking)

**1.1 MinionTemplate**

In `src/models/minion_template.py`:
- Remove all CONFIG_FIELDS as dataclass fields (model, permission_mode, allowed_tools, sandbox_*, docker_*, etc.)
- Remove `template_overrides: dict[str, Any]` field
- Remove the `_EXCLUDED_FROM_CONFIG_FIELDS` constant (no longer needed; identity fields are explicit, everything else is in `config`)
- Add `config: dict[str, Any] = field(default_factory=dict)`
- Keep flat: `template_id, name, role, description, capabilities, profile_ids, system_prompt, created_at, updated_at`
- Update `__post_init__` to no-op for config; keep list/dict defaulting only for `profile_ids`/`capabilities`
- Rewrite `to_dict()`: emit alphabetized keys; include `config` dict
- Rewrite `from_dict()`:
  - Accept new shape directly when `config` key present
  - Otherwise (legacy): see Phase 3 migration helper

**1.2 SessionInfo**

In `src/session_manager.py`:
- Remove all flat CONFIG_FIELDs from the dataclass (permission_mode, model, allowed_tools, docker_*, etc.)
- Remove `session_overrides: dict[str, Any]` field
- Add `config: dict[str, Any] = field(default_factory=dict)`
- Keep flat: `session_id, project_id, name, order, state, working_directory, current_permission_mode` (live runtime, distinct from `config["permission_mode"]`), `is_processing, processing_started_at, error_message, template_id, created_at, updated_at`, plus all legion fields (`is_minion, role, is_overseer, parent_overseer_id, child_minion_ids, capabilities, initialization_context`)
- Update `__post_init__`: drop CONFIG_FIELD defaulting; keep list/dict defaulting only for legion lists/dicts
- Rewrite `to_dict()`/`from_dict()` analogously

**1.3 SessionConfig**

In `src/session_config.py`:
- No structural change to CONFIG_FIELDS set
- Add `DEFAULTS: dict[str, Any]` derived from `SessionConfig().model_dump()` filtered to CONFIG_FIELDS — used by migration

**Phase 1 acceptance:** code compiles; `from_dict({"config": {...}, ...})` round-trips; legacy data fails to load until Phase 3 lands.

#### Phase 2 — Resolution Rewrite

In `src/config_resolution.py`:

Rewrite `resolve_effective_config(session_info, template_manager, profile_manager)`:

```python
async def resolve_effective_config(session_info, template_manager, profile_manager=None):
    config_data = {}

    # Layer 1: profile values + template.config (if template linked)
    if session_info.template_id:
        template = await template_manager.get_template(session_info.template_id)
        if template:
            profile_cache = {}
            for area, profile_id in (template.profile_ids or {}).items():
                profile = await _load_profile_cached(profile_id, profile_manager, profile_cache)
                if profile:
                    for k, v in profile.config.items():
                        config_data[k] = _coerce_list(v) if k in _LIST_FIELDS else v
            for k, v in (template.config or {}).items():
                config_data[k] = v

    # Layer 2: session.config (highest)
    for k, v in (session_info.config or {}).items():
        config_data[k] = v

    # Filter unknown keys (orphaned fields from removed CONFIG_FIELDS)
    known = {k: v for k, v in config_data.items() if k in CONFIG_FIELDS}
    known["template_id"] = session_info.template_id
    return SessionConfig(**known)
```

Rewrite `resolve_template_config(template, profile_manager)` analogously (no session layer, used by minion-spawn path).

**Delete** `_config_from_session_info()`.

**Phase 2 acceptance:** All scenarios in `src/tests/test_config_resolution.py` pass after test rewrite. Replace-semantics from #1226 still hold (last-wins precedence is the only rule).

#### Phase 3 — Migration with Backup

**3.1 Storage helper**

Create `src/storage_utils.py`:

```python
import json
from pathlib import Path

def write_alphabetized_json(path: Path, data: dict, indent: int = 2) -> None:
    """Write dict to path as JSON with alphabetized keys (recursive via sort_keys=True)."""
    payload = json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")
```

Replace all `json.dump`/`Path.write_text(json.dumps(...))` writes in `template_manager.py`, `session_manager.py`, `profile_manager.py` with this helper.

**3.2 Template migration**

In `src/template_manager.py`, add at module scope:

```python
def _migrate_template_to_config_dict(raw: dict) -> tuple[dict, bool]:
    """Promote pre-1230 flat fields + template_overrides into a `config` dict.

    Returns (migrated_raw, changed). Idempotent: if `config` is already present,
    returns input unchanged with changed=False.
    """
    if "config" in raw:
        return raw, False

    from .session_config import CONFIG_FIELDS, SessionConfig
    defaults = {k: getattr(SessionConfig(), k, None) for k in CONFIG_FIELDS}

    config: dict = {}

    # Promote non-default flat fields (defaults are conservatively dropped — the
    # template was implicitly inheriting them anyway via the resolution chain).
    for field_name in CONFIG_FIELDS:
        if field_name not in raw:
            continue
        value = raw.pop(field_name)
        if value is None:
            continue
        if value == defaults.get(field_name):
            continue
        config[field_name] = value

    # All template_overrides entries were explicit user intent — promote unconditionally.
    overrides = raw.pop("template_overrides", None) or {}
    for k, v in overrides.items():
        if k in CONFIG_FIELDS:
            config[k] = v

    raw["config"] = config
    return raw, True
```

In `TemplateManager.load_templates()`, before parsing each JSON:
1. Read raw JSON
2. If `"config"` not in data: trigger backup (see 3.4), then `_migrate_template_to_config_dict(raw)`
3. If migration changed the dict: `write_alphabetized_json(json_path, raw)`
4. Parse via `MinionTemplate.from_dict(raw)`

**3.3 Session migration**

In `src/session_manager.py`, add at module scope:

```python
def _migrate_session_to_config_dict(raw: dict) -> tuple[dict, bool]:
    """Promote pre-1230 flat fields + session_overrides into a `config` dict.

    For template-linked sessions: drop stale flat fields without promotion (they
    were stale-by-design under #1059). Promote `session_overrides` entries.

    For non-template-linked legacy sessions: promote non-default flat fields
    (this was the only place the user could express config). Equivalent to the
    legacy `_config_from_session_info` chain made explicit at storage time.

    Returns (migrated_raw, changed). Idempotent.
    """
    if "config" in raw:
        return raw, False

    from .session_config import CONFIG_FIELDS, SessionConfig
    defaults = {k: getattr(SessionConfig(), k, None) for k in CONFIG_FIELDS}

    config: dict = {}
    template_linked = bool(raw.get("template_id"))

    if template_linked:
        # Drop flat CONFIG_FIELDs without promotion; they were stale-by-design.
        for field_name in list(CONFIG_FIELDS):
            raw.pop(field_name, None)
        # Promote session_overrides entries.
        for k, v in (raw.pop("session_overrides", None) or {}).items():
            if k in CONFIG_FIELDS:
                config[k] = v
    else:
        # Legacy non-template session: flat fields ARE the user's config.
        for field_name in CONFIG_FIELDS:
            if field_name not in raw:
                continue
            value = raw.pop(field_name)
            if value is None:
                continue
            if value == defaults.get(field_name):
                continue
            config[field_name] = value
        # session_overrides on legacy sessions should be empty, but handle safely.
        for k, v in (raw.pop("session_overrides", None) or {}).items():
            if k in CONFIG_FIELDS:
                config[k] = v

    raw["config"] = config
    return raw, True
```

In `SessionManager._load_existing_sessions()`, for each `state.json`:
1. Read raw JSON
2. If `"config"` not in data: trigger backup (see 3.4), then `_migrate_session_to_config_dict(raw)`
3. If migration changed the dict: `write_alphabetized_json(state_path, raw)`
4. Parse via `SessionInfo.from_dict(raw)`

**3.4 Pre-migration backup**

Module-level helper in `src/storage_utils.py`:

```python
import shutil
from datetime import datetime

def backup_legacy_dir_once(source_dir: Path, marker_subdir: str = ".legacy_pre_1230") -> bool:
    """Copy *.json (and *.md for templates) from source_dir into <source>/<marker>/<ts>/ once.

    No-op if marker_subdir already exists in source_dir. Returns True if backup created.
    """
    marker = source_dir / marker_subdir
    if marker.exists():
        return False
    # Only back up if there is actually unmigrated data to protect.
    has_legacy = any(
        "config" not in (p.stat().st_size and __load_safe(p) or {})
        for p in source_dir.glob("*.json")
    )
    if not has_legacy:
        return False
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = marker / timestamp
    target.mkdir(parents=True, exist_ok=True)
    for path in source_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, target / path.name)
    return True
```

For sessions: backup is per-session-directory (each `data/sessions/<uuid>/state.json`). Implementation copies the session directory tree under `data/sessions/.legacy_pre_1230/<ts>/<uuid>/` (templates are flat files; session backup is recursive).

**Phase 3 acceptance:**
- Pre-migration template with `model: "opus"`, `template_overrides: {permission_mode: "plan"}`, and a default `docker_enabled: false` migrates to `config: {model: "opus", permission_mode: "plan"}` with the default-valued field omitted.
- Pre-migration template-linked session with `session_overrides: {model: "haiku"}` and stale flat `model: "sonnet"` migrates to `config: {model: "haiku"}`.
- Pre-migration legacy non-template session with `model: "opus"` migrates to `config: {model: "opus"}`.
- Backup directory contains pre-migration files; second server start does not re-backup.
- All migrated files written alphabetized.

#### Phase 4 — API + Storage + Caller Audit

**4.1 API endpoints** (`src/web_server.py`)

Templates:
- `POST /api/templates`: request body `{name, role?, description?, capabilities?, profile_ids?, system_prompt?, config?}`. Response: full template including `config`.
- `PUT /api/templates/{id}`: same. Backend overwrites the named fields; absent `config` field means no change; explicit `config: {}` clears all set fields.
- `GET /api/templates`: returns full templates.
- Reject any request body containing `template_overrides` with HTTP 400 ("template_overrides removed in #1230; use config dict").

Sessions:
- `POST /api/sessions`: request body `{name, working_directory, project_id?, template_id?, config?}`.
- `PATCH /api/sessions/{id}`: request body `{name?, config?}`. The `config` value, if provided, replaces the entire session.config dict (this is the layer's full state — set/reset is expressed by the dict's content).
- `GET /api/sessions/{id}`: returns `{session, effective_config}` as today; `session` now includes `config` instead of flat fields and `session_overrides`.
- Reject any request body containing `session_overrides` with HTTP 400.

**4.2 Caller audit**

Replace direct `session_info.<config_field>` reads with effective_config lookups. Affected files (verified during exploration):
- `src/proxy_addon.py` — `docker_proxy_allowlist_domains`, `assigned_secrets`, `docker_proxy_image`
- `src/secret_injection.py` — `assigned_secrets`
- `src/permission_resolver.py` — `allowed_tools`, `disallowed_tools`, `setting_sources`, `additional_directories`
- `src/claude_sdk.py` — verify SessionConfig is the input (likely already true)
- `src/session_coordinator.py` `start_session()` — confirm effective_config is computed once and threaded to all consumers (this was partially done by #1117/#1059)

Pattern for callers that previously took `SessionInfo` and read flat fields: change signature to take `SessionConfig` (or accept both and read from `effective_config` parameter).

**Phase 4 acceptance:**
- API contract tests confirm new shape for both endpoints.
- Tests confirm rejected requests with legacy keys.
- Proxy/secret injection/permission resolution all read effective values, verified with a test session whose template_id resolves through profile + template.config.

#### Phase 5 — Frontend

**5.1 New composable: `useFieldState`**

Create `frontend/src/composables/useFieldState.js`:

```js
import { computed } from 'vue';
import { CONFIG_FIELDS_LIST, FIELD_DEFAULTS } from '@/utils/configFields';

/**
 * Compute per-field state from the resolution chain.
 *
 * @param {Object} args
 * @param {Object} args.layerConfig         - reactive ref/computed: current layer's config dict (the dict being edited)
 * @param {string} args.layerKind           - 'template' | 'session'
 * @param {Object} args.template            - linked template (for session edit) or null
 * @param {Object} args.profilesByArea      - { area: profile } map for inherited profiles
 *
 * Returns computed map: fieldName -> { effective_value, source, source_label, is_set_here, current_layer_value }
 */
export function useFieldState({ layerConfig, layerKind, template, profilesByArea }) {
  return computed(() => {
    const result = {};
    for (const field of CONFIG_FIELDS_LIST) {
      let value = FIELD_DEFAULTS[field];
      let source = 'default';
      let source_label = 'Default';

      // Profile layer (only for fields whose area has a linked profile)
      const profile = profilesByArea.value?.[fieldArea(field)];
      if (profile && field in (profile.config || {})) {
        value = profile.config[field];
        source = 'profile';
        source_label = `From profile: ${profile.name}`;
      }

      // Template layer (only when editing a session linked to a template)
      if (layerKind === 'session' && template?.value && field in (template.value.config || {})) {
        value = template.value.config[field];
        source = 'template';
        source_label = `From template: ${template.value.name}`;
      }

      // Current layer
      const cfg = layerConfig.value || {};
      const isSetHere = field in cfg;
      if (isSetHere) {
        value = cfg[field];
        source = layerKind;
        source_label = layerKind === 'template' ? 'Set on this template' : 'Set on this session';
      }

      result[field] = {
        effective_value: value,
        source,
        source_label,
        is_set_here: isSetHere,
        current_layer_value: isSetHere ? cfg[field] : undefined,
      };
    }
    return result;
  });
}
```

**5.2 ConfigurationModal.vue refactor**

- Remove `fieldStates`, `profileOriginalValues`, `valueAfterProfileMerge` data
- Add `layerConfig: ref({})` — the dict being edited (mirrors `template.config` or `session.config` depending on mode)
- Add `useFieldState({...})` to derive per-field render state
- `populateFormFromTemplate(t)`: `layerConfig.value = { ...(t.config || {}) }`; identity fields populated separately
- `populateFormFromSession(s, effectiveConfig)`: `layerConfig.value = { ...(s.config || {}) }`; identity fields (name, working_directory) populated separately
- `onFieldChange(field, value)`: `layerConfig.value = { ...layerConfig.value, [field]: value }`
- `onFieldReset(field)`: `const next = { ...layerConfig.value }; delete next[field]; layerConfig.value = next`
- `extractPayload(mode)`:
  - `template` (create/edit): `{ name, role, description, capabilities, profile_ids, system_prompt, config: layerConfig.value }`
  - `session` (create): `{ name, working_directory, template_id, config: layerConfig.value }`
  - `session` (update): `{ name, config: layerConfig.value }`
- Drop the empty-list-to-null `toPayload` transforms entirely. `[]` is a valid stored value.

**5.3 FieldRenderer.vue rewrite**

Replace badge logic:
- Show small source label next to field label: e.g. "From profile: Default Permissions" or "Default" or "Set on this template"
- If `field_state.is_set_here`: show small reset icon (e.g. ↺) inline; on click → emit `reset` event with field name
- The displayed widget value is `field_state.effective_value`
- On widget input → emit `change` with `(field, newValue)`; ConfigurationModal writes into `layerConfig`

**5.4 Pinia store updates**

- `frontend/src/stores/session.js` `patchSession(id, updates)`: payload shape `{name?, config?}`; remove any references to `session_overrides` or flat config fields
- `frontend/src/stores/profile.js`: no change

**5.5 Build**

- `cd frontend && npm run build`
- Commit `frontend/dist/`

**Phase 5 acceptance:**
- Open template edit: every field shows source label; only fields in `template.config` show reset icon; clicking reset removes from dict and re-derives from chain
- Open session edit (template-linked): same with one extra layer (template values appear with "From template: X" label)
- Save records only what's in `layerConfig`; payload is `{config: {...}}`
- Empty list `[]` in the UI saves as `[]` in the JSON (round-trip verified)

#### Phase 6 — Cleanup & Tests

**6.1 Code deletion**

- `_config_from_session_info()` in `config_resolution.py`
- `_init_session_overrides()` in `session_coordinator.py`
- `_track_overrides()` in `session_manager.py`
- `skip_flat` plumbing in `session_manager.create_session()`
- `template_overrides`-related code paths
- `session_overrides`-related code paths
- `frontend/src/components/configuration/ConfigurationModal.vue` `toPayload` empty-list-to-null transforms
- 4-state `fieldStates` machinery and `profileOriginalValues` map

**6.2 New / updated tests**

`src/tests/test_config_resolution.py` — rewrite for new chain. Each scenario from issue acceptance criteria:
1. Template field follows profile when not set
2. Template explicitly overrides profile
3. Template explicitly sets to empty list (`[]` honored)
4. Reset reverts template field to inherited (storage-level: field absent from `template.config` after reset)
5. Profile field removal falls through to default
6. Session-level reset
7. Session field follows template
8. Session explicitly differs from template

`src/tests/test_template_manager.py` — migration tests:
- Round-trip: pre-1230 template with flat fields + template_overrides → post-migration `config` dict; idempotent on second load
- Default-valued flat fields are dropped during migration (per Phase 3.2)
- Template_overrides entries are promoted unconditionally
- Backup directory created on first migration; not re-created on second

`src/tests/test_session_manager.py` — migration tests:
- Pre-1230 template-linked session with `session_overrides` migrates correctly; stale flat fields dropped
- Pre-1230 legacy non-template session with flat fields migrates with non-default values promoted
- Backup directory created once

`src/tests/test_web_server.py` — API contract:
- POST/PUT template with `config` dict round-trips
- POST/PUT template with `template_overrides` returns 400
- PATCH session with `config` dict round-trips
- PATCH session with `session_overrides` returns 400

`src/tests/test_storage_utils.py` (new) — alphabetized JSON helper

**6.3 Lint**

- `uv run ruff check --fix src/` must pass with zero violations on all changed files

---

### Testing Strategy

**Unit:**
- All new and updated tests above
- Existing 1300+ test suite must continue to pass

**Manual end-to-end** (UI flow):
1. Start server with pre-1230 data dir; verify migration runs once, backups created, server starts cleanly
2. Open template list; edit a template that previously had flat fields; verify all fields show source labels (default/profile/inherited); verify only previously-set fields show reset icon
3. Set a field; save; reopen; verify the set field has `is_set_here=true` and reset icon
4. Click reset on a set field; save; reopen; verify field reverts to inherited and reset icon is gone
5. Set a list field to `[]` (clear all tags); save; reopen; verify field stays empty (this is the #1229 acceptance scenario, now structurally guaranteed)
6. Repeat 2-5 for a template-linked session edit modal (chain has one extra layer)
7. Restart server; verify state.json files have alphabetized keys; verify same behavior survives restart
8. Inspect `data/templates/.legacy_pre_1230/<ts>/` and `data/sessions/.legacy_pre_1230/<ts>/`; verify pre-migration content preserved

**Migration corpus**:
- Hand-craft test fixtures representing pre-1230 templates (flat fields, with and without template_overrides) and pre-1230 sessions (template-linked with overrides; legacy non-template with flat fields). Place under `src/tests/fixtures/legacy_pre_1230/`. Migration tests load these, run migration, assert resulting shape.

---

### Risks & Considerations

**Risk: pre-existing live sessions with active SDK clients during migration.**
- Mitigation: migration runs only on initial load (server start). Live sessions are restarted on the new server. The accepted-risk envelope from the issue allows recreating affected sessions.

**Risk: migration writes JSON files in place — partial failure could corrupt state.**
- Mitigation: pre-migration backup directory makes any recovery a copy operation. Migration is idempotent: a second pass on a half-migrated file (one with `config` already present) is a no-op. JSON write helper writes the full payload atomically (single `path.write_text`); no partial-write window meaningful at this size.

**Risk: callers of session_info.<flat_field> outside the explicitly-audited list.**
- Mitigation: phase 4 caller audit relies on grep across the repo. Given the dataclass field is removed in phase 1, any unaudited caller will fail at runtime with `AttributeError` rather than silently reading None. CI test suite catches these.

**Risk: profile JSON does not need schema migration but still gets rewritten alphabetized — touches files unnecessarily.**
- Mitigation: alphabetization happens lazily on next save. No forced rewrite of existing profile files at server start. Acceptable to let profile files become alphabetized over time as they're edited.

**Risk: frontend `npm run build` output drift breaks atomic merge.**
- Mitigation: rebuild dist at PR finalize time; verify with manual smoke test against built bundle (not just `npm run dev`).

**Risk: the issue's "default-equals-explicit" choice (Q1=store) means migrated templates may be slightly larger than necessary.**
- Note: migration drops default-valued flat fields conservatively (Phase 3.2 logic). Users explicitly setting a default at runtime after this PR will see it stored. This is consistent with intent but worth confirming during manual testing.

**Risk: orphan-key handling at resolution time relies on `if k in CONFIG_FIELDS` filter.**
- Tested by adding a test that loads a session with `config: {removed_field_x: "value"}` and asserts no load error and the unknown key does not appear in effective_config.

**Out-of-band dependency:** #1229 ships first as a stopgap. After this PR, the fix in #1229 (frontend empty-list-to-null transform tweak or backend "skip if None" change) becomes redundant — the new `config` dict makes `[]` distinct from "absent". The redundant code should be removed as part of phase 6 cleanup.

---

### Estimated Scope

**Lines of code changed (rough order):**
- Backend data model + resolution + migration: ~600 lines added/modified, ~400 deleted
- Backend API + caller audit: ~200 lines modified
- Backend tests: ~500 lines added
- Frontend modal + composable + field renderer: ~600 lines modified, ~300 deleted
- Frontend dist rebuild: auto

**Phase boundaries are testable** — backend phases 1–4 can be tested against the existing frontend by writing curl-level integration tests for the new API shape. Frontend phase 5 lands and the modal refactor is testable manually before phase 6 cleanup.

**Single PR, single branch**: feat/issue-1230. Phases sequenced as commits inside the branch. Post-phase-2 is a tested intermediate state (resolution works on migrated data); post-phase-5 is a tested intermediate state (full UI works); post-phase-6 ships.

Estimate: medium-large. Plan accommodates 2–3 review cycles given the scope.

---

Plan approved by User/Agent. Ready for implementation.
