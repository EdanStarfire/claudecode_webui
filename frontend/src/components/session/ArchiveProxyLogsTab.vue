<template>
  <div class="archive-proxy-logs-tab">
    <div v-if="archiveTabsStore.isLoadingTabs(sessionId)" class="empty-placeholder">
      <span class="icon">⏳</span>Loading…
    </div>
    <div v-else-if="capturedLogs.length === 0" class="empty-placeholder">
      <span class="icon">🔌</span>Proxy was not enabled for this session — no logs were captured.
    </div>
    <template v-else>
      <div
        v-for="name in PROXY_LOG_NAMES"
        :key="name"
        class="proxy-log-row"
        :class="{ open: logState[name].expanded, disabled: !isCaptured(name) }"
      >
        <div class="proxy-log-header" @click="isCaptured(name) && toggleLog(name)">
          <span class="chevron">{{ isCaptured(name) ? (logState[name].expanded ? '▾' : '▸') : '·' }}</span>
          <span class="name">{{ name }}</span>
          <span v-if="isCaptured(name)" class="meta">
            {{ logMeta(name).line_count }} lines · {{ formatFileSize(logMeta(name).size_bytes) }}
          </span>
          <span v-else class="meta disabled">not captured for this session</span>
        </div>

        <div v-if="logState[name].expanded" class="proxy-log-body">
          <div v-if="logState[name].loading" class="loading-row">Loading…</div>
          <template v-else>
            <div class="log-lines">
              <div v-for="(line, idx) in logState[name].head?.lines || []" :key="idx" class="log-line">
                <span class="ln">{{ logState[name].head.startLine + idx + 1 }}</span><span>{{ line }}</span>
              </div>
            </div>
            <div v-if="logState[name].tail" class="log-gap">
              <span class="gap-label">
                {{ logState[name].tail.startLine - logState[name].head.endLine }} lines hidden
              </span>
              <button :disabled="logState[name].loadingMore" @click="loadMore(name)">
                {{ logState[name].loadingMore ? 'Loading…' : 'Load more ⇕' }}
              </button>
            </div>
            <div v-if="logState[name].tail" class="log-lines">
              <div v-for="(line, idx) in logState[name].tail.lines" :key="idx" class="log-line">
                <span class="ln">{{ logState[name].tail.startLine + idx + 1 }}</span><span>{{ line }}</span>
              </div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { formatFileSize } from '@/utils/fileTypes'

const props = defineProps({
  sessionId: { type: String, required: true },
  archiveId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const PROXY_LOG_NAMES = ['access.log', 'dns.log', 'socks5.log', 'dropped.log']
const HEAD_CHUNK = 50
const TAIL_CHUNK = 50
const LOAD_MORE_CHUNK = 50

const logState = reactive(
  Object.fromEntries(
    PROXY_LOG_NAMES.map((name) => [
      name,
      { expanded: false, loading: false, loaded: false, loadingMore: false, head: null, tail: null },
    ])
  )
)

const capturedLogs = computed(() => archiveTabsStore.proxyLogsForSession(props.sessionId))

function logMeta(name) {
  return capturedLogs.value.find((l) => l.name === name)
}

function isCaptured(name) {
  return !!logMeta(name)
}

async function toggleLog(name) {
  const state = logState[name]
  if (state.expanded) {
    state.expanded = false
    return
  }
  state.expanded = true
  if (state.loaded || state.loading) return

  state.loading = true
  try {
    const lineCount = logMeta(name)?.line_count ?? 0
    if (lineCount <= HEAD_CHUNK + TAIL_CHUNK) {
      const result = await archiveTabsStore.loadProxyLogContent(
        props.sessionId, props.archiveId, name, { offset: 0, limit: HEAD_CHUNK + TAIL_CHUNK }
      )
      if (result) {
        state.head = { lines: result.lines, startLine: result.start_line, endLine: result.end_line }
        state.tail = null
      }
    } else {
      const [headResult, tailResult] = await Promise.all([
        archiveTabsStore.loadProxyLogContent(
          props.sessionId, props.archiveId, name, { offset: 0, limit: HEAD_CHUNK }
        ),
        archiveTabsStore.loadProxyLogContent(
          props.sessionId, props.archiveId, name, { limit: TAIL_CHUNK, fromEnd: true }
        ),
      ])
      if (headResult) {
        state.head = { lines: headResult.lines, startLine: headResult.start_line, endLine: headResult.end_line }
      }
      if (tailResult) {
        state.tail = { lines: tailResult.lines, startLine: tailResult.start_line, endLine: tailResult.end_line }
      }
    }
    state.loaded = true
  } finally {
    state.loading = false
  }
}

async function loadMore(name) {
  const state = logState[name]
  if (!state.head || !state.tail || state.loadingMore) return
  const remaining = state.tail.startLine - state.head.endLine
  if (remaining <= 0) return

  state.loadingMore = true
  let result
  try {
    const limit = Math.min(LOAD_MORE_CHUNK, remaining)
    result = await archiveTabsStore.loadProxyLogContent(
      props.sessionId, props.archiveId, name, { offset: state.head.endLine, limit }
    )
  } finally {
    state.loadingMore = false
  }
  if (!result) return

  // Guard against a response that makes no forward progress (e.g. the backend
  // clamps to 0 lines for this offset) — without this, "Load more" could stay
  // clickable forever without the gap ever closing. Only append lines/advance
  // endLine when the response actually moved forward; a non-advancing response
  // is instead treated as "nothing more to fetch" and folds the tail in as-is.
  if (result.end_line > state.head.endLine) {
    state.head.lines = [...state.head.lines, ...result.lines]
    state.head.endLine = result.end_line
  } else {
    state.head.endLine = state.tail.startLine
  }

  // Head has caught up to tail — fold tail in and drop the gap divider.
  if (state.head.endLine >= state.tail.startLine) {
    state.head.lines = [...state.head.lines, ...state.tail.lines]
    state.head.endLine = state.tail.endLine
    state.tail = null
  }
}
</script>

<style scoped>
.empty-placeholder {
  text-align: center;
  color: var(--bs-secondary-color);
  font-style: italic;
  font-size: 12px;
  padding: 36px 10px;
}
.empty-placeholder .icon {
  font-size: 22px;
  display: block;
  margin-bottom: 6px;
  opacity: 0.6;
}

.proxy-log-row {
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  margin-bottom: 8px;
  overflow: hidden;
}
.proxy-log-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  cursor: pointer;
  background: var(--bs-secondary-bg);
}
.proxy-log-row.disabled .proxy-log-header {
  cursor: default;
}
.proxy-log-header .chevron {
  transition: transform 0.15s;
  display: inline-block;
  font-size: 11px;
}
.proxy-log-header .name {
  font-family: monospace;
  font-weight: 600;
}
.proxy-log-header .meta {
  color: var(--bs-secondary-color);
  font-size: 12px;
}
.proxy-log-header .meta.disabled {
  font-style: italic;
}
.proxy-log-body {
  border-top: 1px solid var(--bs-border-color);
}
.loading-row {
  padding: 10px 12px;
  font-size: 12px;
  color: var(--bs-secondary-color);
}
.log-lines {
  font-family: "SF Mono", Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.6;
  padding: 8px 12px;
  max-height: 320px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-line {
  display: flex;
  gap: 10px;
}
.log-line .ln {
  color: var(--bs-secondary-color);
  flex: 0 0 34px;
  text-align: right;
  user-select: none;
}
.log-gap {
  text-align: center;
  padding: 8px;
  background: var(--table-stripe-bg);
  border-top: 1px dashed var(--bs-border-color);
  border-bottom: 1px dashed var(--bs-border-color);
}
.log-gap .gap-label {
  font-size: 11px;
  color: var(--bs-secondary-color);
  margin-right: 8px;
}
.log-gap button {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 10px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-body-bg);
  cursor: pointer;
  color: var(--bs-link-color);
}
</style>
