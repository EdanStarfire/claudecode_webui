<template>
  <div class="shell-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="shell-info">
        <span class="shell-icon">{{ toolIcon }}</span>
        <strong>{{ toolLabel }}:</strong>
        <code v-if="shellId" class="shell-id">Shell {{ shellId }}</code>
      </div>

      <!-- BashOutput specific params -->
      <div v-if="toolCall.name === 'BashOutput' && filter" class="filter-info mt-2">
        <strong>Filter:</strong>
        <code class="filter-pattern">{{ filter }}</code>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="toolCall.result" class="tool-section">
      <div class="result-header mb-2">
        <strong>{{ resultLabel }}:</strong>
        <span v-if="shellStatus" class="badge" :class="statusBadgeClass">{{ shellStatus }}</span>
      </div>

      <!-- Stdout output -->
      <div v-if="parsedResult?.stdout" class="mb-2">
        <div class="output-label">Output:</div>
        <div class="shell-output">
          <pre><code>{{ parsedResult.stdout }}</code></pre>
        </div>
      </div>

      <!-- Stderr output -->
      <div v-if="parsedResult?.stderr" class="mb-2">
        <div class="output-label text-danger">
          ❗ Errors:
        </div>
        <div class="shell-output shell-output-error">
          <pre><code>{{ parsedResult.stderr }}</code></pre>
        </div>
      </div>

      <!-- Success/Error message (shown when no stdout/stderr) -->
      <div v-if="!parsedResult?.stdout && !parsedResult?.stderr">
        <div v-if="toolCall.result.success !== false">
          <ToolSuccessMessage :message="resultMessage" />
        </div>
        <div v-else class="tool-error" style="padding: var(--tool-padding, 6px 8px);">
          ❗ {{ toolCall.result.error || 'Operation failed' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ToolSuccessMessage from './ToolSuccessMessage.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const toolIcon = computed(() => {
  if (props.toolCall.name === 'BashOutput') return '📤'
  if (props.toolCall.name === 'KillShell') return '🛑'
  return '🐚'
})

const toolLabel = computed(() => {
  if (props.toolCall.name === 'BashOutput') return 'Reading Shell Output'
  if (props.toolCall.name === 'KillShell') return 'Terminating Shell'
  return 'Shell Operation'
})

const resultLabel = computed(() => {
  if (props.toolCall.name === 'BashOutput') return 'Output'
  if (props.toolCall.name === 'KillShell') return 'Status'
  return 'Result'
})

const shellId = computed(() => {
  return props.toolCall.input?.bash_id || props.toolCall.input?.shell_id
})

const filter = computed(() => {
  return props.toolCall.input?.filter
})

// Helper to extract content from XML-like tags
function extractTagContent(text, tag) {
  if (!text) return null
  const regex = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'i')
  const match = text.match(regex)
  return match ? match[1].trim() : null
}

// Parse the tool result content (which may contain XML-like tags)
const parsedResult = computed(() => {
  const result = props.toolCall.result
  if (!result) return null

  // Extract the actual content - it might be wrapped in result.content
  let contentToParse = result
  if (result.content) {
    contentToParse = result.content
  }

  // If content is a string, parse XML tags
  if (typeof contentToParse === 'string') {
    return {
      status: extractTagContent(contentToParse, 'status'),
      stdout: extractTagContent(contentToParse, 'stdout'),
      stderr: extractTagContent(contentToParse, 'stderr'),
      exit_code: extractTagContent(contentToParse, 'exit_code')
    }
  }

  // If content is an array (Claude SDK format)
  if (Array.isArray(contentToParse)) {
    const textContent = contentToParse.find(c => c.type === 'text')?.text || ''
    return {
      status: extractTagContent(textContent, 'status'),
      stdout: extractTagContent(textContent, 'stdout'),
      stderr: extractTagContent(textContent, 'stderr'),
      exit_code: extractTagContent(textContent, 'exit_code')
    }
  }

  // Otherwise return the result as-is (fallback for object format)
  return {
    status: result.status || contentToParse.status,
    stdout: result.stdout || result.output || contentToParse.stdout || contentToParse.output,
    stderr: result.stderr || contentToParse.stderr,
    exit_code: result.exit_code || contentToParse.exit_code
  }
})

const outputContent = computed(() => {
  if (props.toolCall.name === 'BashOutput') {
    const parsed = parsedResult.value
    if (!parsed) return null

    // Combine stdout and stderr if both exist
    const parts = []
    if (parsed.stdout) parts.push(parsed.stdout)
    if (parsed.stderr) parts.push(`[stderr]\n${parsed.stderr}`)

    return parts.length > 0 ? parts.join('\n\n') : null
  }
  return null
})

const shellStatus = computed(() => {
  const parsed = parsedResult.value
  return parsed?.status
})

const statusBadgeClass = computed(() => {
  const status = shellStatus.value
  if (status === 'running') return 'bg-success'
  if (status === 'completed') return 'bg-secondary'
  if (status === 'killed' || status === 'terminated') return 'bg-danger'
  return 'bg-info'
})

const resultMessage = computed(() => {
  if (props.toolCall.result?.message) {
    return props.toolCall.result.message
  }

  if (props.toolCall.name === 'KillShell') {
    return 'Shell terminated successfully'
  }

  if (props.toolCall.name === 'BashOutput') {
    const parsed = parsedResult.value
    if (parsed?.status === 'running' && !parsed.stdout && !parsed.stderr) {
      return 'No new output since last check'
    }
    if (parsed?.status === 'failed') {
      return `Shell failed (exit code ${parsed.exit_code || 'unknown'})`
    }
  }

  return 'Operation completed'
})

const summary = computed(() => `${toolLabel.value}: Shell ${shellId.value || ''}`)
const params = computed(() => ({ shell_id: shellId.value, filter: filter.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.shell-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  padding: 0.75rem;
  background: var(--tool-bg, #f8fafc);
  border-radius: var(--tool-radius, 4px);
}

.shell-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.shell-icon {
  font-size: 1.25rem;
}

.shell-id {
  padding: 0.25rem 0.5rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-code-font-size, 11px);
  font-family: 'Courier New', monospace;
}

.filter-info {
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.filter-pattern {
  padding: 0.25rem 0.5rem;
  background: #fff3cd;
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-code-font-size, 11px);
  font-family: 'Courier New', monospace;
  color: #856404;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
}

.output-label {
  font-weight: 600;
  margin-bottom: 0.25rem;
  font-size: 0.875rem;
}

.shell-output {
  background: #1e1e1e;
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.shell-output pre {
  margin: 0;
  padding: 0.75rem;
  max-height: var(--tool-code-max-height, 200px);
  overflow: auto;
  color: #d4d4d4;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  line-height: 1.4;
  white-space: pre;
}

.shell-output-error {
  background: #2d1f1f;
  border: 1px solid #dc3545;
}

.shell-output-error pre {
  color: #f48771;
}

.tool-error {
  color: #dc3545;
}
</style>
