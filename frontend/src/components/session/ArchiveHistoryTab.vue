<template>
  <div class="archive-history-tab">
    <div v-if="archiveTabsStore.isLoadingTabs(sessionId)" class="empty-placeholder">
      <span class="icon">⏳</span>Loading…
    </div>
    <div v-else-if="historyFiles.length === 0" class="empty-placeholder">
      <span class="icon">🕘</span>No history was captured for this session.
    </div>
    <template v-else>
      <div
        v-for="file in sortedHistoryFiles"
        :key="file.name"
        class="hist-row"
        :class="{ active: selectedFile === file.name }"
        @click="selectFile(file.name)"
      >
        <span class="name">{{ file.name }}</span>
        <span class="size">{{ formatFileSize(file.size_bytes) }}</span>
      </div>

      <div class="md-preview">
        <div v-if="contentState?.loading" class="loading-row">Loading…</div>
        <div v-else-if="contentState?.error" class="error-row">Failed to load this history file.</div>
        <MarkdownView v-else-if="contentState?.content" :content="contentState.content" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { formatFileSize } from '@/utils/fileTypes'
import MarkdownView from '@/components/common/MarkdownView.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  archiveId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const historyFiles = computed(() => archiveTabsStore.historyForSession(props.sessionId))
// Newest first by mtime — the backend lists history/*.md via a plain lexicographic
// glob sort (oldest-first, since filenames are ascending unix-timestamp-prefixed),
// so this can't rely on listing order.
const sortedHistoryFiles = computed(() =>
  [...historyFiles.value].sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
)
const selectedFile = ref(null)

const contentState = computed(() => {
  if (!selectedFile.value) return null
  return archiveTabsStore.historyContent(props.sessionId, props.archiveId, selectedFile.value)
})

function selectFile(name) {
  selectedFile.value = name
  archiveTabsStore.loadHistoryContent(props.sessionId, props.archiveId, name)
}

// Auto-select the most recent history file once the listing loads.
watch(sortedHistoryFiles, (files) => {
  if (!selectedFile.value && files.length > 0) {
    selectFile(files[0].name)
  }
}, { immediate: true })
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

.hist-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--bs-border-color);
  font-size: 13px;
  cursor: pointer;
}
.hist-row:hover {
  background: var(--table-stripe-bg);
}
.hist-row.active {
  background: rgba(var(--bs-primary-rgb), 0.08);
}
.hist-row .name {
  flex: 1;
  font-family: monospace;
}
.hist-row .size {
  color: var(--bs-secondary-color);
  font-size: 11.5px;
}
.md-preview {
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  margin-top: 10px;
  padding: 16px;
}
.loading-row,
.error-row {
  font-size: 12px;
  color: var(--bs-secondary-color);
}
</style>
