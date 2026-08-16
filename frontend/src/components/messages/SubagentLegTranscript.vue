<template>
  <div class="leg-transcript">
    <!-- Prompt (collapsed by default) -->
    <div v-if="prompt" class="leg-prompt">
      <div class="prompt-toggle" @click.stop="promptCollapsed = !promptCollapsed">
        <svg class="chevron" :class="{ expanded: !promptCollapsed }" width="10" height="10" viewBox="0 0 12 12">
          <path d="M4.5 2L8.5 6L4.5 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        Prompt
        <span class="char-count">({{ prompt.length }} chars)</span>
        <a v-if="prompt.length > 500" class="view-full-link" @click.stop="openFullPrompt">View Full</a>
      </div>
      <pre v-if="!promptCollapsed" class="prompt-content">{{ promptDisplay }}</pre>
    </div>

    <!-- Narration (thinking/text captured from the subagent's own turns — issue #1671) -->
    <div v-if="narration.length > 0" class="leg-narration">
      <div
        v-for="(msg, idx) in narration"
        :key="msg.message_id || msg.id || idx"
        class="narration-entry"
      >
        <MarkdownView v-if="msg.content" class="narration-text" :content="msg.content" />
        <div v-if="msg.thinking" class="narration-thinking">{{ msg.thinking }}</div>
      </div>
    </div>

    <!-- Child tool calls -->
    <ActivityTimeline
      v-if="childTools.length > 0"
      :tools="childTools"
      :messageId="legToolCall.id"
    />
    <div v-else-if="isRunning" class="leg-placeholder">
      <span class="placeholder-spinner"></span>
      Working...
    </div>
    <div v-else class="leg-placeholder leg-placeholder-done">
      No tool activity recorded
    </div>

    <!-- Result -->
    <div v-if="hasResult" class="leg-result" :class="{ 'leg-result-error': isError }">
      <div class="result-toggle" @click.stop="resultCollapsed = !resultCollapsed">
        <svg class="chevron" :class="{ expanded: !resultCollapsed }" width="10" height="10" viewBox="0 0 12 12">
          <path d="M4.5 2L8.5 6L4.5 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        {{ isError ? 'Error:' : 'Result:' }}
        <a v-if="isResultTruncated" class="view-full-link" @click.stop="openFullResult">View Full</a>
      </div>
      <pre v-if="!resultCollapsed && resultSummary" class="result-content">{{ resultSummary }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import MarkdownView from '@/components/common/MarkdownView.vue'
import ActivityTimeline from './tools/ActivityTimeline.vue'

const props = defineProps({
  taskId: {
    type: String,
    default: null
  },
  legIndex: {
    type: Number,
    required: true
  },
  // The Task/Agent tool call that launched/resumed this specific leg — carries the
  // prompt/subagent_type input and (once available) the tool result.
  legToolCall: {
    type: Object,
    required: true
  },
  isRunning: {
    type: Boolean,
    default: false
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const resourceStore = useResourceStore()

const promptCollapsed = ref(true)
const resultCollapsed = ref(true)

// Issue #1746 (stage: subagents) follow-up: a resume leg's own launching tool call is
// SendMessage(to, summary, message) rather than Task/Agent(prompt, description) — fall back to
// `message` so the resumed leg's own follow-up instructions still show in its Prompt panel.
const prompt = computed(() => props.legToolCall.input?.prompt || props.legToolCall.input?.message || null)

const promptDisplay = computed(() => {
  if (!prompt.value) return ''
  return prompt.value.length > 500 ? prompt.value.slice(0, 500) + '...' : prompt.value
})

function openFullPrompt() {
  resourceStore.openWithDirectContent('Subagent Prompt', prompt.value)
}

const narration = computed(() => messageStore.narrationForLeg(props.taskId, props.legIndex))

// Issue #1746 (stage: subagents) follow-up: child tools for THIS leg specifically, resolved by
// timestamp window (see message.js's childToolCallsForLeg) — NOT by matching parent_tool_use_id
// against this leg's own launch/resume tool_use_id. Real repro data confirmed parent_tool_use_id
// on ALL of a subagent's activity (original run and every resume) stays pinned to the very
// first leg's own id, so an exact-id match here would always attribute everything to leg 0.
const childTools = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return []
  return messageStore.childToolCallsForLeg(sessionId, props.taskId, props.legIndex)
})

const hasResult = computed(() => props.legToolCall.result != null)
const isError = computed(() => props.legToolCall.result?.error === true)

const fullResultContent = computed(() => {
  if (!hasResult.value) return ''
  const content = props.legToolCall.result?.content || props.legToolCall.result?.message || ''
  if (typeof content !== 'string') return JSON.stringify(content, null, 2)
  return content
})

const resultSummary = computed(() => {
  if (!hasResult.value) return null
  const content = fullResultContent.value
  return content.length > 500 ? content.slice(0, 500) + '...' : content
})

const isResultTruncated = computed(() => fullResultContent.value.length > 500)

function openFullResult() {
  resourceStore.openWithDirectContent('Subagent Result', fullResultContent.value)
}
</script>

<style scoped>
.leg-transcript {
  padding: 6px 10px 8px 10px;
  border-left: 2px solid var(--bs-border-color);
  margin-left: 4px;
}

.leg-prompt,
.leg-result {
  margin-top: 6px;
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  overflow: hidden;
}

.leg-result-error {
  border-color: var(--tool-error-border);
}

.prompt-toggle,
.result-toggle {
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  background: var(--subagent-panel-chip-bg, var(--bs-tertiary-bg));
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 4px;
}

.prompt-toggle:hover,
.result-toggle:hover {
  background: var(--bs-secondary-bg);
}

.leg-result-error .result-toggle {
  background: var(--tool-error-bg);
  color: var(--tool-error-text);
}

.chevron {
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.chevron.expanded {
  transform: rotate(90deg);
}

.char-count {
  color: var(--bs-secondary-color);
  font-weight: 400;
  font-style: italic;
}

.view-full-link {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  color: var(--bs-link-color);
  cursor: pointer;
  text-decoration: none;
}

.view-full-link:hover {
  text-decoration: underline;
}

.prompt-content,
.result-content {
  margin: 0;
  padding: 8px;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  line-height: 1.4;
  color: var(--bs-body-color);
  background: var(--subagent-code-chip-bg, var(--bs-body-bg));
  border-top: 1px solid var(--bs-border-color);
}

.leg-narration {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.narration-entry {
  font-size: 12px;
}

.narration-text {
  color: var(--bs-body-color);
}

.narration-thinking {
  color: var(--bs-secondary-color);
  font-style: italic;
  font-size: 11px;
  white-space: pre-wrap;
}

.leg-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  color: var(--agent-badge-accent, var(--bs-secondary-color));
  font-size: 12px;
  font-style: italic;
}

.leg-placeholder-done {
  color: var(--bs-secondary-color);
}

.placeholder-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--bs-border-color);
  border-top-color: var(--agent-badge-bg, var(--bs-primary));
  border-radius: 50%;
  animation: leg-spin 0.8s linear infinite;
}

@keyframes leg-spin {
  to { transform: rotate(360deg); }
}
</style>
