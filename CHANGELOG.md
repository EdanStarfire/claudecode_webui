# Changelog

All notable changes to claudecode_webui are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-05-25

### Added
- **Collapsible right sidebar panels** — replaced tab bar with stacked expandable sections (#1540)
- **Schedules** — session-scoped panel, disposable repeat-count scheduling, and inline `__new__` creation flow (#1539, #1541, #1562)
- **Agent-registered persistent links** — agents can pin links to their session panel (#1543)
- **Video resource support** — `.webm`/`.mp4` files displayable in resource gallery (#1555)
- **MCP reconnect button** — always visible in server detail, not just on error (#1554)
- **Unread session state** — orange indicator for completed-but-unviewed sessions (#1517)
- **Secrets section redesign** — matches settings layout (#1561)

### Fixed
- **Edits panel** — repaired data display (backend block detection), removed `/dev/null` false positives, fixed "just now" timestamps, and resolved dark mode unreadable text with intrusive banner replaced by tooltip (#1564, #1566, #1568)
- **Session navigation** — autoscroll race condition, instant switch-back via KeepAlive, scroll position persistence, and duplicate loadMessages on deep-link (#1518, #1521, #1525, #1528, #1529, #1557)
- **Emoticon escaping** — letter-based emoticons no longer mangled in markdown (#1534)
- **Agent ordering** — consistent sort in segmented session bar (#1535)

### Changed
- **Rebranding to ccWebUI** — new favicon, header logo, PWA manifest, and auth-exempt public assets (#1556, #1558, #1559, #1560)
- **claude-agent-sdk → 0.2.85** (#1533)

[1.1.0]: https://github.com/EdanStarfire/claudecode_webui/releases/tag/v1.1.0

## [1.0.0] - 2026-05-22

### Added
- **Legion multi-agent system** — spawn, manage, reparent, and monitor minion hierarchies within projects; compact tree nodes with role subtitle; per-status background tint on minion cards; minion count badge navigates to project overview
- **LiteLLM provider catalog** — full multi-provider model routing with per-tier model assignment, drop_params toggle, per-instance storage, and proxy log UI
- **Scheduled message system** — cron-based schedules with script-type support, conditional filter scripts, history rotation with aggregate metrics, and MCP tools for minion self-management (`create_schedule`, `update_schedule`)
- **OAuth 2.1 vault secrets** — import, auto-refresh, and reconnect recovery for OAuth tokens; proxy credential injection with per-chunk stream scrubbing for MCP, scheduled scripts, and SSE
- **Shared upstream MCP connections** — opt-in passthrough to upstream MCP servers with shared connection lifecycle management
- **Secret references in MCP headers** — HTTP MCP server header values can reference vault secrets
- **Docker proxy enhancements** — allow-all outbound sentinel (`*`), proxy log UI, ZlibError handling for compressed responses
- **Message Queue UX** — clear history button, schedule name label with full-prompt tooltip, timestamps on every queue item
- **Reparent minions** within the legion hierarchy via the session management modal
- **Analytics improvements** — 4-series token cost breakdown chart, mobile viewport support, continuous time axis
- **Alphabetical sort** for agent chips and minion tree nodes
- **Configurable token pricing** UI in app settings
- **Image rendering** in ResourceFullView when a Read tool result contains image data
- **Reconnect button** for OAuth2 vault secrets visible in all health states

### Changed
- Markdown rendering migrated from `marked` / `DOMPurify` / `mermaid` to `@comark/vue` — full MDC component support, mermaid diagrams, KaTeX math, Cytoscape graphs, external links open in new tab
- MCP Servers section moved from Application to Library in settings sidebar
- Session memory now uses Claude Code's built-in memory system instead of a custom implementation
- GitHub token provisioned via vault secret injection instead of `~/.config/gh` host mount
- `claude-agent-sdk` bumped to `0.2.82` with corresponding CLI updates
- Full dark-theme audit — hardcoded colours replaced with Bootstrap 5.3 CSS custom properties across all UI components

### Fixed
- Cross-task cancel scope violation in `SharedMcpConnectionManager` — OAuth token refresh no longer crashes MCP connections (#1506)
- Silent consumer death — watchdog detects when `consume_all_responses` exits unexpectedly and surfaces a visible error instead of a frozen session (#1508)
- Chip navigation desync — `currentSessionId` now commits synchronously on click so URL, chip highlight, and message list always agree (#1509)
- Session message contamination — switching sessions now remounts `MessageList`, preventing previous session's messages from bleeding through (#1512)
- comark `:digit` crash — bare port references like `:8001` in assistant messages no longer throw `InvalidCharacterError` (#1516)
- comark frontmatter blanking — messages beginning with `---` no longer render blank (#1495)
- System messages excluded from latest_message display in the minion tree (#1499)
- Proxy credential scrubbing for OAuth MCP, scheduled scripts, and SSE (#1426)
- Analytics turn recording, schedule error counter, and numerous targeted UI and backend fixes

[1.0.0]: https://github.com/EdanStarfire/claudecode_webui/releases/tag/v1.0.0
