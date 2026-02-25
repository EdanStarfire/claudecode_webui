<template>
  <div class="msg-wrapper msg-system">
    <div
      class="msg-pill"
      :class="{
        'pill-compaction': isCompactionStatus,
        'pill-stderr': isStderr,
        'pill-hook': isHook && !isHookError,
        'pill-hook-error': isHookError,
        'pill-expandable': isHook
      }"
      @click="toggleExpand"
    >
      <span v-if="isStderr" class="pill-icon">&#x26A0;&#xFE0F;</span>
      <span v-else-if="isCompactionStatus" class="pill-icon">&#x1F5DC;&#xFE0F;</span>
      <span v-else-if="isHook" class="pill-icon">&#x2699;&#xFE0F;</span>
      <span class="pill-text">{{ displayContent }}</span>
      <span class="pill-sep">&middot;</span>
      <span class="pill-time">{{ formattedTimestamp }}</span>
      <span v-if="isHook" class="pill-chevron" :class="{ 'chevron-open': expanded }">&#x25B6;</span>
    </div>
    <div v-if="isHook && expanded" class="hook-detail">
      <pre class="hook-json">{{ hookJson }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const expanded = ref(false)

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

// Check if this is a compaction status message
const isCompactionStatus = computed(() => {
  return props.message.metadata?.subtype === 'status' &&
         props.message.metadata?.init_data?.status === 'compacting'
})

// Check if this is an error-class message (stderr or session failure)
const isStderr = computed(() => {
  const subtype = props.message.metadata?.subtype
  return subtype === 'stderr' || subtype === 'session_failed'
})

// Check if this is a hook message (hook_started or hook_response)
const isHook = computed(() => {
  const subtype = props.message.metadata?.subtype
  return subtype === 'hook_started' || subtype === 'hook_response'
})

// Check if this is a hook error (non-zero exit code)
const isHookError = computed(() => {
  if (!isHook.value) return false
  const subtype = props.message.metadata?.subtype
  if (subtype !== 'hook_response') return false
  const exitCode = props.message.metadata?.exit_code ?? props.message.metadata?.init_data?.exit_code ?? props.message.metadata?.init_data?.exitCode
  return exitCode !== undefined && exitCode !== null && exitCode !== 0
})

const displayContent = computed(() => {
  if (isCompactionStatus.value) {
    return 'Context compaction in progress...'
  }
  const content = props.message.content || ''
  // Allow longer display for stderr messages since they often contain important error details
  const maxLen = isStderr.value ? 200 : 80
  if (content.length > maxLen) {
    return content.substring(0, maxLen - 3) + '...'
  }
  return content
})

// Raw hook data for expanded view
const hookJson = computed(() => {
  const data = props.message.metadata?.init_data
  if (!data) return '{}'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
})

function toggleExpand() {
  if (isHook.value) {
    expanded.value = !expanded.value
  }
}
</script>

<style scoped>
.msg-wrapper {
  padding: 8px 16px;
}

.msg-system {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.msg-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  max-width: 80%;
}

.pill-expandable {
  cursor: pointer;
  user-select: none;
}

.pill-expandable:hover {
  filter: brightness(0.96);
}

.pill-compaction {
  background: #fffbea;
  border-color: #fde68a;
}

.pill-stderr {
  background: #fef2f2;
  border-color: #fecaca;
}

.pill-hook {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.pill-hook .pill-text {
  color: #1e40af;
}

.pill-hook-error {
  background: #fef2f2;
  border-color: #fecaca;
}

.pill-hook-error .pill-text {
  color: #991b1b;
}

.pill-stderr .pill-text {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  color: #991b1b;
  white-space: normal;
  word-break: break-word;
}

.pill-icon {
  font-size: 12px;
}

.pill-text {
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pill-sep {
  font-size: 12px;
  color: #94a3b8;
}

.pill-time {
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
}

.pill-chevron {
  font-size: 8px;
  color: #94a3b8;
  transition: transform 0.15s ease;
  display: inline-block;
}

.chevron-open {
  transform: rotate(90deg);
}

.hook-detail {
  margin-top: 4px;
  max-width: 80%;
  width: 100%;
}

.hook-json {
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 14px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
}
</style>
