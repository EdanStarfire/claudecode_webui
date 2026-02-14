<template>
  <div class="diff-panel">
    <!-- Panel Body -->
    <div class="diff-body">
      <!-- Loading -->
      <div v-if="diffStore.loading && !diffStore.currentDiff" class="text-center py-4">
        <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
        <p class="mb-0 small text-muted mt-2">Loading diff...</p>
      </div>

      <!-- Not a git repo -->
      <div v-else-if="diffStore.currentDiff && !diffStore.isGitRepo" class="empty-placeholder">
        <span>Not a git repository</span>
      </div>

      <!-- Error from backend (e.g., no remote) -->
      <div v-else-if="diffStore.currentDiff && diffStore.currentDiff.error" class="empty-placeholder">
        <span>{{ diffStore.currentDiff.error }}</span>
      </div>

      <!-- API error -->
      <div v-else-if="diffStore.error" class="text-danger text-center py-4">
        <p class="mb-0 small">{{ diffStore.error }}</p>
      </div>

      <!-- No changes -->
      <div v-else-if="diffStore.currentDiff && diffStore.fileCount === 0" class="empty-placeholder">
        <span>No changes since main</span>
      </div>

      <!-- Diff content -->
      <div v-else-if="diffStore.currentDiff && diffStore.fileCount > 0">
        <!-- Mode Toggle -->
        <div class="mode-toggle d-flex align-items-center gap-1 px-3 py-2 border-bottom">
          <button
            class="btn btn-sm"
            :class="diffStore.currentMode === 'total' ? 'btn-primary' : 'btn-outline-secondary'"
            @click="diffStore.setMode('total')"
          >
            Total Changes
          </button>
          <button
            class="btn btn-sm"
            :class="diffStore.currentMode === 'commits' ? 'btn-primary' : 'btn-outline-secondary'"
            @click="diffStore.setMode('commits')"
          >
            By Commit
          </button>
          <button
            class="btn btn-link p-0 ms-auto refresh-btn"
            @click="refresh"
            title="Refresh diff"
            :disabled="diffStore.loading"
          >
            <svg :class="{ 'spin': diffStore.loading }" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 3a5 5 0 1 0 4.546 2.914.5.5 0 0 1 .908-.417A6 6 0 1 1 8 2v1z"/>
              <path d="M8 4.466V.534a.25.25 0 0 1 .41-.192l2.36 1.966c.12.1.12.284 0 .384L8.41 4.658A.25.25 0 0 1 8 4.466z"/>
            </svg>
          </button>
        </div>

        <!-- Stats bar -->
        <div class="stats-bar px-3 py-1 border-bottom small text-muted">
          <span class="text-success">+{{ diffStore.currentDiff.total_insertions }}</span>
          <span class="mx-1">/</span>
          <span class="text-danger">-{{ diffStore.currentDiff.total_deletions }}</span>
          <span class="ms-2">{{ diffStore.fileCount }} file{{ diffStore.fileCount !== 1 ? 's' : '' }}</span>
          <span v-if="diffStore.commitCount > 0" class="ms-2">{{ diffStore.commitCount }} commit{{ diffStore.commitCount !== 1 ? 's' : '' }}</span>
        </div>

        <!-- Total Changes Mode -->
        <div v-if="diffStore.currentMode === 'total'" class="file-list">
          <div
            v-for="file in diffStore.currentFiles"
            :key="file.path"
            class="file-item d-flex align-items-center gap-2 px-3 py-2 border-bottom"
            @click="openFile(file.path, 'uncommitted')"
          >
            <span class="status-icon" :class="'status-' + file.status">
              {{ statusIcon(file.status) }}
            </span>
            <span class="file-path text-truncate flex-grow-1" :title="file.path">
              {{ file.path }}
            </span>
            <span v-if="!file.is_binary" class="file-stats small text-nowrap">
              <span v-if="file.insertions > 0" class="text-success">+{{ file.insertions }}</span>
              <span v-if="file.insertions > 0 && file.deletions > 0" class="text-muted">/</span>
              <span v-if="file.deletions > 0" class="text-danger">-{{ file.deletions }}</span>
            </span>
            <span v-else class="file-stats small text-muted">binary</span>
          </div>
        </div>

        <!-- By Commit Mode -->
        <div v-else class="commit-list">
          <div
            v-for="commit in diffStore.currentDiff.commits"
            :key="commit.hash"
            class="commit-item border-bottom"
          >
            <div
              class="commit-header d-flex align-items-center gap-2 px-3 py-2"
              @click="toggleCommit(commit.hash)"
            >
              <svg class="chevron-icon" :class="{ expanded: expandedCommits.has(commit.hash) }" width="10" height="10" viewBox="0 0 12 12">
                <path d="M4.5 2L8.5 6L4.5 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
              </svg>
              <span v-if="commit.is_uncommitted" class="wip-badge">WIP</span>
              <code v-else class="commit-hash">{{ commit.short_hash }}</code>
              <span class="commit-message text-truncate flex-grow-1" :title="commit.message">
                {{ commit.message }}
              </span>
              <span v-if="!commit.is_uncommitted" class="commit-date small text-muted text-nowrap">
                {{ formatDate(commit.date) }}
              </span>
            </div>
            <div v-if="expandedCommits.has(commit.hash)" class="commit-files">
              <div
                v-for="filePath in commit.files"
                :key="filePath"
                class="file-item d-flex align-items-center gap-2 px-4 py-1"
                @click="openFile(filePath, commit.is_uncommitted ? 'uncommitted' : null)"
              >
                <span class="status-icon" :class="'status-' + getFileStatus(filePath)">
                  {{ statusIcon(getFileStatus(filePath)) }}
                </span>
                <span class="file-path text-truncate small" :title="filePath">
                  {{ filePath }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- No data loaded yet -->
      <div v-else class="empty-placeholder">
        <span>No diff data available</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useDiffStore } from '@/stores/diff'
import { useSessionStore } from '@/stores/session'

const diffStore = useDiffStore()
const sessionStore = useSessionStore()

const expandedCommits = ref(new Set())

function refresh() {
  if (sessionStore.currentSessionId) {
    diffStore.refreshDiff(sessionStore.currentSessionId)
  }
}

function openFile(filePath, fileRef = null) {
  if (sessionStore.currentSessionId) {
    diffStore.openFullView(sessionStore.currentSessionId, filePath, fileRef)
  }
}

function toggleCommit(hash) {
  if (expandedCommits.value.has(hash)) {
    expandedCommits.value.delete(hash)
  } else {
    expandedCommits.value.add(hash)
  }
  expandedCommits.value = new Set(expandedCommits.value)
}

function statusIcon(status) {
  switch (status) {
    case 'added': return '+'
    case 'deleted': return '−'
    case 'renamed': return 'R'
    default: return 'M'
  }
}

function getFileStatus(filePath) {
  if (!diffStore.currentDiff || !diffStore.currentDiff.files) return 'modified'
  return diffStore.currentDiff.files[filePath]?.status || 'modified'
}

function formatDate(isoDate) {
  if (!isoDate) return ''
  const date = new Date(isoDate)
  const now = new Date()
  const diffMs = now - date
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

// Auto-load diff when session changes
watch(
  () => sessionStore.currentSessionId,
  (newId) => {
    if (newId && !diffStore.diffBySession.get(newId)) {
      diffStore.loadDiff(newId)
    }
  },
  { immediate: true }
)

// Auto-expand uncommitted commit when diff data loads
watch(
  () => diffStore.currentDiff,
  (diff) => {
    if (diff?.commits) {
      const uncommitted = diff.commits.find(c => c.is_uncommitted)
      if (uncommitted) {
        expandedCommits.value.add(uncommitted.hash)
        expandedCommits.value = new Set(expandedCommits.value)
      }
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.diff-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.refresh-btn {
  color: #6c757d;
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}

.refresh-btn:hover {
  color: #495057;
}

.refresh-btn .spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.diff-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.empty-placeholder {
  text-align: center;
  padding: 32px 16px;
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

.mode-toggle .btn-sm {
  font-size: 0.75rem;
  padding: 2px 8px;
}

.stats-bar {
  background-color: #f8f9fa;
  font-size: 0.78rem;
}

.file-item {
  cursor: pointer;
  font-size: 0.82rem;
}

.file-item:hover {
  background-color: #f0f0f0;
}

.status-icon {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-weight: 700;
  font-size: 0.75rem;
  width: 16px;
  text-align: center;
  flex-shrink: 0;
}

.status-added { color: #198754; }
.status-deleted { color: #dc3545; }
.status-modified { color: #fd7e14; }
.status-renamed { color: #0d6efd; }

.file-path {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.78rem;
}

.file-stats {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.72rem;
}

.chevron-icon {
  transition: transform 0.2s ease;
}

.chevron-icon.expanded {
  transform: rotate(90deg);
}

.commit-header {
  cursor: pointer;
  font-size: 0.82rem;
}

.commit-header:hover {
  background-color: #f0f0f0;
}

.commit-hash {
  font-size: 0.72rem;
  color: #0d6efd;
  background: #e7f1ff;
  padding: 1px 4px;
  border-radius: 3px;
  flex-shrink: 0;
}

.wip-badge {
  font-size: 0.68rem;
  font-weight: 700;
  color: #856404;
  background: #fff3cd;
  padding: 1px 6px;
  border-radius: 3px;
  flex-shrink: 0;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.commit-message {
  font-size: 0.8rem;
}

.commit-date {
  font-size: 0.7rem;
}

.commit-files {
  background-color: #fafafa;
}

.commit-files .file-item {
  font-size: 0.78rem;
  border-bottom: none;
}

.commit-files .file-item:hover {
  background-color: #f0f0f0;
}
</style>
