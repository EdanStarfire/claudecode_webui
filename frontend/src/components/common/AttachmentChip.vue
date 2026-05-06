<template>
  <span
    class="attachment-chip"
    :class="{ clickable: isClickable }"
    :title="isClickable ? 'Click to preview' : filename"
    @click="handleClick"
  >
    <span class="attachment-chip-icon">{{ icon }}</span>
    <span class="attachment-chip-name">{{ filename }}</span>
    <span v-if="formattedSize" class="attachment-chip-size">{{ formattedSize }}</span>
    <a
      v-if="downloadUrl && !resourceId"
      :href="downloadUrl"
      class="attachment-chip-dl"
      target="_blank"
      @click.stop
    >⬇</a>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { getFileIcon, getFileIconByMimeType } from '@/utils/fileTypes'

const props = defineProps({
  filename:    { type: String,  required: true },
  size:        { type: Number,  default: null },
  mimeType:    { type: String,  default: null },
  resourceId:  { type: String,  default: null },
  sessionId:   { type: String,  default: null },
  downloadUrl: { type: String,  default: null },
})

const emit = defineEmits(['preview'])

const icon = computed(() =>
  props.mimeType ? getFileIconByMimeType(props.mimeType) : getFileIcon(props.filename)
)

const formattedSize = computed(() => {
  if (!props.size) return ''
  const bytes = props.size
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
})

const isClickable = computed(() => !!props.resourceId || !!props.downloadUrl)

function handleClick() {
  if (props.resourceId) {
    emit('preview')
  }
}
</script>

<style scoped>
.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 12px;
  font-size: 12px;
  font-family: 'Courier New', monospace;
  color: var(--bs-body-color);
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip.clickable {
  cursor: pointer;
  color: var(--bs-link-color);
}

.attachment-chip.clickable:hover {
  background: var(--bs-tertiary-bg);
  border-color: var(--bs-link-color);
}

.attachment-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip-size {
  color: var(--bs-secondary-color);
  font-size: 10px;
  flex-shrink: 0;
}

.attachment-chip-dl {
  text-decoration: none;
  color: var(--bs-link-color);
  flex-shrink: 0;
}

.attachment-chip-dl:hover {
  color: var(--bs-link-hover-color);
}
</style>
