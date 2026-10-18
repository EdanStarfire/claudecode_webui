<template>
  <div class="archive-attachments-tab">
    <div v-if="archiveTabsStore.isLoadingTabs(sessionId)" class="empty-placeholder">
      <span class="icon">⏳</span>Loading…
    </div>
    <div v-else-if="attachments.length === 0" class="empty-placeholder">
      <span class="icon">📎</span>No attachments were recorded for this session.
    </div>
    <template v-else>
      <div v-for="att in attachments" :key="att.name" class="att-row">
        <span class="icon">{{ getFileIcon(att.name) }}</span>
        <span class="name">{{ att.name }}</span>
        <span class="size">{{ formatFileSize(att.size_bytes) }}</span>
        <a class="dl-btn" :href="downloadUrl(att.name)" :download="att.name">⬇ Download</a>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { getFileIcon, formatFileSize } from '@/utils/fileTypes'

const props = defineProps({
  sessionId: { type: String, required: true },
  archiveId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const attachments = computed(() => archiveTabsStore.attachmentsForSession(props.sessionId))

function downloadUrl(filename) {
  return archiveTabsStore.getAttachmentDownloadUrl(props.sessionId, props.archiveId, filename)
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

.att-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--bs-border-color);
  font-size: 13px;
}
.att-row:hover {
  background: var(--table-stripe-bg);
}
.att-row .icon {
  font-size: 16px;
  flex: 0 0 20px;
}
.att-row .name {
  flex: 1;
  font-family: monospace;
}
.att-row .size {
  color: var(--bs-secondary-color);
  font-size: 11.5px;
  flex: 0 0 70px;
  text-align: right;
}
.dl-btn {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 4px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  color: var(--bs-body-color);
  text-decoration: none;
  cursor: pointer;
}
</style>
