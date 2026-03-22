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
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid #c7d2fe;
  border-radius: 12px;
  font-size: 12px;
  font-family: 'Courier New', monospace;
  color: #374151;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip.clickable {
  cursor: pointer;
  color: #3730a3;
}

.attachment-chip.clickable:hover {
  background: rgba(238, 242, 255, 0.9);
  border-color: #818cf8;
}

.attachment-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip-size {
  color: #94a3b8;
  font-size: 10px;
  flex-shrink: 0;
}

.attachment-chip-dl {
  text-decoration: none;
  color: #3730a3;
  flex-shrink: 0;
}

.attachment-chip-dl:hover {
  color: #1e1b8a;
}
</style>
