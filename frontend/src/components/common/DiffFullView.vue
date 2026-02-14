<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="isOpen"
        class="diff-full-view-overlay"
        @click="handleOverlayClick"
        @keydown="handleKeydown"
        tabindex="0"
        ref="overlayRef"
      >
        <!-- Close Button -->
        <button
          class="close-btn"
          @click.stop="closeFullView"
          title="Close (Escape)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>

        <!-- Navigation - Previous -->
        <button
          v-if="totalFiles > 1"
          class="nav-btn nav-prev"
          @click.stop="diffStore.prevFile()"
          title="Previous file (Left Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>

        <!-- Diff Content Container -->
        <div class="diff-container" @click.stop>
          <!-- Header -->
          <div class="diff-header">
            <div class="diff-header-info">
              <h5 class="diff-filename">
                {{ diffStore.currentFilePath }}
              </h5>
              <div class="diff-meta">
                <span v-if="commitInfo" class="commit-ref-badge">
                  <code>{{ commitInfo.short_hash }}</code>
                  <span class="commit-ref-msg">{{ commitInfo.message }}</span>
                </span>
                <span v-if="currentFileInfo" class="meta-badge" :class="'status-' + currentFileInfo.status">
                  {{ currentFileInfo.status }}
                </span>
                <span v-if="currentFileInfo && !currentFileInfo.is_binary" class="meta-item">
                  <span class="text-success">+{{ currentFileInfo.insertions }}</span>
                  /
                  <span class="text-danger">-{{ currentFileInfo.deletions }}</span>
                </span>
              </div>
            </div>
            <button
              class="copy-btn"
              @click.stop="copyDiff"
              :title="copyFeedback ? 'Copied!' : 'Copy diff to clipboard'"
              :disabled="!diffContent"
            >
              <svg v-if="copyFeedback" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span class="copy-label">{{ copyFeedback ? 'Copied!' : 'Copy' }}</span>
            </button>
          </div>

          <!-- Diff Body -->
          <div class="diff-body">
            <!-- Loading -->
            <div v-if="fileDiffEntry && fileDiffEntry.loading" class="diff-loading">
              <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
              <span>Loading diff...</span>
            </div>

            <!-- Error -->
            <div v-else-if="fileDiffEntry && fileDiffEntry.error" class="diff-error">
              Failed to load diff: {{ fileDiffEntry.error }}
            </div>

            <!-- Diff content -->
            <div v-else-if="diffContent" class="diff-content">
              <table class="diff-table">
                <tbody>
                  <tr v-for="(line, idx) in parsedLines" :key="idx" :class="lineClass(line)">
                    <td class="line-num line-num-old">{{ line.oldNum || '' }}</td>
                    <td class="line-num line-num-new">{{ line.newNum || '' }}</td>
                    <td class="line-content">
                      <span class="line-prefix">{{ line.prefix }}</span>
                      <span>{{ line.text }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- No diff -->
            <div v-else class="diff-empty">
              No diff available for this file.
            </div>
          </div>
        </div>

        <!-- Navigation - Next -->
        <button
          v-if="totalFiles > 1"
          class="nav-btn nav-next"
          @click.stop="diffStore.nextFile()"
          title="Next file (Right Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </button>

        <!-- Position Indicator -->
        <div v-if="totalFiles > 1" class="position-indicator">
          {{ currentFileIndex + 1 }} / {{ totalFiles }}
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, ref, nextTick, onUnmounted } from 'vue'
import { useDiffStore } from '@/stores/diff'

const diffStore = useDiffStore()
const overlayRef = ref(null)
const copyFeedback = ref(false)
let copyTimeout = null

const isOpen = computed(() => diffStore.fullViewOpen)

const fileDiffEntry = computed(() => diffStore.currentFullViewFileDiff)
const diffContent = computed(() => fileDiffEntry.value?.content || null)

const totalFiles = computed(() => diffStore.fullViewFiles.length)
const currentFileIndex = computed(() => {
  if (!diffStore.currentFilePath) return 0
  return diffStore.fullViewFiles.findIndex(f => f.path === diffStore.currentFilePath)
})

const currentFileInfo = computed(() => {
  if (!diffStore.currentFilePath || !diffStore.currentDiff?.files) return null
  return diffStore.currentDiff.files[diffStore.currentFilePath] || null
})

const commitInfo = computed(() => {
  const ref = diffStore.fullViewRef
  if (!ref || ref === 'uncommitted') return null
  const diff = diffStore.currentDiff
  if (!diff?.commits) return null
  return diff.commits.find(c => c.hash === ref) || null
})

/**
 * Parse unified diff output into structured lines
 */
const parsedLines = computed(() => {
  if (!diffContent.value) return []

  const lines = diffContent.value.split('\n')
  const result = []
  let oldLine = 0
  let newLine = 0

  for (const raw of lines) {
    // Skip diff header lines
    if (raw.startsWith('diff --git') || raw.startsWith('index ') ||
        raw.startsWith('---') || raw.startsWith('+++') ||
        raw.startsWith('new file') || raw.startsWith('deleted file') ||
        raw.startsWith('similarity') || raw.startsWith('rename') ||
        raw.startsWith('Binary')) {
      if (raw.startsWith('Binary')) {
        result.push({ type: 'info', prefix: '', text: raw, oldNum: null, newNum: null })
      }
      continue
    }

    // Hunk header
    if (raw.startsWith('@@')) {
      const match = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/)
      if (match) {
        oldLine = parseInt(match[1])
        newLine = parseInt(match[2])
        result.push({ type: 'hunk', prefix: '', text: raw, oldNum: null, newNum: null })
      }
      continue
    }

    // Context line
    if (raw.startsWith(' ') || (raw.length > 0 && !raw.startsWith('+') && !raw.startsWith('-') && !raw.startsWith('\\'))) {
      result.push({ type: 'context', prefix: ' ', text: raw.substring(1), oldNum: oldLine, newNum: newLine })
      oldLine++
      newLine++
    }
    // Added line
    else if (raw.startsWith('+')) {
      result.push({ type: 'add', prefix: '+', text: raw.substring(1), oldNum: null, newNum: newLine })
      newLine++
    }
    // Removed line
    else if (raw.startsWith('-')) {
      result.push({ type: 'remove', prefix: '-', text: raw.substring(1), oldNum: oldLine, newNum: null })
      oldLine++
    }
    // "No newline at end of file" or empty line
    else if (raw.startsWith('\\')) {
      result.push({ type: 'info', prefix: '', text: raw, oldNum: null, newNum: null })
    }
  }

  return result
})

function lineClass(line) {
  return `diff-line diff-line-${line.type}`
}

function closeFullView() {
  diffStore.closeFullView()
}

function handleOverlayClick(event) {
  if (event.target === event.currentTarget) {
    closeFullView()
  }
}

function handleKeydown(event) {
  switch (event.key) {
    case 'Escape':
      closeFullView()
      break
    case 'ArrowLeft':
      diffStore.prevFile()
      break
    case 'ArrowRight':
      diffStore.nextFile()
      break
  }
}

async function copyDiff() {
  if (!diffContent.value) return
  try {
    await navigator.clipboard.writeText(diffContent.value)
    copyFeedback.value = true
    if (copyTimeout) clearTimeout(copyTimeout)
    copyTimeout = setTimeout(() => {
      copyFeedback.value = false
    }, 2000)
  } catch {
    console.error('Failed to copy to clipboard')
  }
}

// Focus overlay when opened
watch(isOpen, (open) => {
  if (open) {
    nextTick(() => {
      overlayRef.value?.focus()
    })
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

onUnmounted(() => {
  document.body.style.overflow = ''
  if (copyTimeout) clearTimeout(copyTimeout)
})
</script>

<style scoped>
.diff-full-view-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  outline: none;
}

.close-btn {
  position: absolute;
  top: 20px;
  right: 20px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
  z-index: 10;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.nav-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 48px;
  height: 80px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
  z-index: 10;
}

.nav-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.nav-prev { left: 20px; }
.nav-next { right: 20px; }

/* Diff Container */
.diff-container {
  max-width: calc(100% - 160px);
  max-height: calc(100% - 120px);
  width: 1000px;
  display: flex;
  flex-direction: column;
  background: #f8f9fa;
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.4);
  overflow: hidden;
}

.diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #dee2e6;
  background: #fff;
  flex-shrink: 0;
}

.diff-header-info {
  min-width: 0;
  flex: 1;
}

.diff-filename {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #212529;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.diff-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 2px;
  font-size: 0.8rem;
}

.meta-badge {
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 600;
  text-transform: uppercase;
}

.meta-badge.status-added { background: #d4edda; color: #155724; }
.meta-badge.status-deleted { background: #f8d7da; color: #721c24; }
.meta-badge.status-modified { background: #fff3cd; color: #856404; }
.meta-badge.status-renamed { background: #cce5ff; color: #004085; }

.commit-ref-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: #495057;
}

.commit-ref-badge code {
  font-size: 0.72rem;
  color: #0d6efd;
  background: #e7f1ff;
  padding: 1px 4px;
  border-radius: 3px;
}

.commit-ref-msg {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-item {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.78rem;
  color: #6c757d;
}

.copy-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #e9ecef;
  border: 1px solid #ced4da;
  border-radius: 6px;
  color: #495057;
  font-size: 0.8rem;
  cursor: pointer;
  transition: background-color 0.2s, color 0.2s;
  flex-shrink: 0;
  margin-left: 12px;
}

.copy-btn:hover:not(:disabled) {
  background: #dee2e6;
  color: #212529;
}

.copy-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.copy-label {
  white-space: nowrap;
}

/* Diff Body */
.diff-body {
  flex: 1;
  overflow-y: auto;
  min-height: 200px;
}

.diff-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #6c757d;
  padding: 24px;
  justify-content: center;
}

.diff-error {
  color: #dc3545;
  padding: 24px;
  text-align: center;
}

.diff-empty {
  color: #6c757d;
  padding: 24px;
  text-align: center;
  font-style: italic;
}

/* Diff Table */
.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.82rem;
  line-height: 1.45;
}

.diff-line td {
  padding: 0 8px;
  vertical-align: top;
  white-space: pre-wrap;
  word-break: break-all;
}

.line-num {
  width: 50px;
  min-width: 50px;
  text-align: right;
  color: #adb5bd;
  user-select: none;
  border-right: 1px solid #dee2e6;
  padding-right: 8px;
  font-size: 0.75rem;
}

.line-content {
  padding-left: 8px;
}

.line-prefix {
  user-select: none;
  display: inline-block;
  width: 1ch;
}

/* Line type colors */
.diff-line-context { background: #fff; }
.diff-line-add { background: #e6ffec; }
.diff-line-add .line-num { background: #ccffd8; }
.diff-line-remove { background: #ffebe9; }
.diff-line-remove .line-num { background: #ffd7d5; }
.diff-line-hunk {
  background: #ddf4ff;
  color: #0969da;
  font-style: italic;
}
.diff-line-hunk .line-num { background: #ddf4ff; }
.diff-line-hunk .line-content { padding: 4px 8px; }
.diff-line-info {
  background: #f6f8fa;
  color: #6c757d;
  font-style: italic;
}

/* Position Indicator */
.position-indicator {
  position: absolute;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  color: white;
  background: rgba(0, 0, 0, 0.5);
  padding: 6px 16px;
  border-radius: 20px;
  font-size: 0.9rem;
}

/* Modal Transition */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

/* Mobile */
@media (max-width: 576px) {
  .nav-btn {
    width: 40px;
    height: 60px;
  }

  .nav-prev { left: 8px; }
  .nav-next { right: 8px; }

  .diff-container {
    max-width: calc(100% - 20px);
    max-height: calc(100% - 80px);
    width: 100%;
  }

  .close-btn {
    top: 10px;
    right: 10px;
  }

  .diff-header {
    padding: 10px 12px;
  }

  .copy-label {
    display: none;
  }
}
</style>
