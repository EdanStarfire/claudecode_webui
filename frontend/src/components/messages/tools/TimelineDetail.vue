<template>
  <div class="timeline-detail" role="region" aria-label="Tool call details">
    <!-- Orphaned Banner -->
    <div v-if="isOrphaned" class="detail-banner detail-banner-warning">
      <span class="banner-icon">⏹️</span>
      <div>
        <strong>Tool Execution Cancelled</strong>
        <p>{{ orphanedInfo?.message || 'Session was terminated' }}</p>
      </div>
    </div>

    <!-- Auto-Approval Details (shown for internally auto-approved tools) -->
    <div v-if="toolCall.autoApprovedReason" class="permission-details permission-auto-approved">
      <div class="permission-status">
        <span class="permission-status-icon" style="color: #a78bfa">⚡</span>
        <span>Auto-approved: {{ toolCall.autoApprovedReason }}</span>
      </div>
    </div>

    <!-- Permission Details (shown after permission resolved) -->
    <div v-if="hasPermissionInfo && !toolCall.autoApprovedReason" class="permission-details" :class="permissionDetailsClass">
      <div class="permission-status">
        <span class="permission-status-icon" :style="{ color: permissionStatusColor }">{{ permissionStatusIcon }}</span>
        <span>{{ permissionStatusLabel }}</span>
      </div>
      <div v-if="hasAppliedUpdates" class="permission-changes">
        <span class="changes-label">Permission rules added:</span>
        <ul>
          <li v-for="(update, index) in toolCall.appliedUpdates" :key="index">
            {{ formatSuggestion(update) }}
          </li>
        </ul>
      </div>
    </div>

    <!-- Tool Handler Content -->
    <component
      v-if="!(isAskUserQuestion && effectiveStatus === 'permission_required')"
      ref="handlerRef"
      :is="toolHandlerComponent"
      :toolCall="toolCall"
    />

    <!-- View Full Result (universal for long results) -->
    <div v-if="hasLongResult" class="view-full-bar">
      <a class="view-full-link" @click.stop="openFullResult">View Full Result</a>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, toRef } from 'vue'
import { useToolStatus } from '@/composables/useToolStatus'
import { useToolResult } from '@/composables/useToolResult'
import { useResourceStore } from '@/stores/resource'
import BaseToolHandler from '@/components/tools/BaseToolHandler.vue'
import ReadToolHandler from '@/components/tools/ReadToolHandler.vue'
import WriteToolHandler from '@/components/tools/WriteToolHandler.vue'
import BashToolHandler from '@/components/tools/BashToolHandler.vue'
import TodoToolHandler from '@/components/tools/TodoToolHandler.vue'
import EditToolHandler from '@/components/tools/EditToolHandler.vue'
import SearchToolHandler from '@/components/tools/SearchToolHandler.vue'
import WebToolHandler from '@/components/tools/WebToolHandler.vue'
import AgentToolHandler from '@/components/tools/AgentToolHandler.vue'
import TaskCreateToolHandler from '@/components/tools/TaskCreateToolHandler.vue'
import TaskUpdateToolHandler from '@/components/tools/TaskUpdateToolHandler.vue'
import TaskListToolHandler from '@/components/tools/TaskListToolHandler.vue'
import TaskGetToolHandler from '@/components/tools/TaskGetToolHandler.vue'
import NotebookEditToolHandler from '@/components/tools/NotebookEditToolHandler.vue'
import ShellToolHandler from '@/components/tools/ShellToolHandler.vue'
import CommandToolHandler from '@/components/tools/CommandToolHandler.vue'
import SkillToolHandler from '@/components/tools/SkillToolHandler.vue'
import SlashCommandToolHandler from '@/components/tools/SlashCommandToolHandler.vue'
import ExitPlanModeToolHandler from '@/components/tools/ExitPlanModeToolHandler.vue'
import AskUserQuestionToolHandler from '@/components/tools/AskUserQuestionToolHandler.vue'
import SendCommToolHandler from '@/components/tools/SendCommToolHandler.vue'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

// Use shared composables
const { effectiveStatus, isOrphaned, orphanedInfo } = useToolStatus(toRef(props, 'toolCall'))
const { resultContent } = useToolResult(toRef(props, 'toolCall'))
const resourceStore = useResourceStore()

// Local state
const handlerRef = ref(null)

// View full result
const hasLongResult = computed(() => {
  if (!resultContent.value) return false
  return resultContent.value.length > 500 || resultContent.value.split('\n').length > 15
})

const imageBlock = computed(() => {
  const content = props.toolCall?.result?.content
  if (!Array.isArray(content)) return null
  for (const block of content) {
    if (block?.type === 'image' && block?.source?.type === 'base64' && block?.source?.data) {
      return {
        data: block.source.data,
        mime: block.source.media_type || 'image/png',
      }
    }
  }
  return null
})

function openFullResult() {
  const toolName = props.toolCall.name || 'Tool'
  if (imageBlock.value) {
    const filePath = props.toolCall.input?.file_path
    const fileName = filePath ? filePath.split('/').pop() : null
    const title = fileName ? `${toolName}: ${fileName}` : `${toolName} Result`
    resourceStore.openWithDirectImage(title, imageBlock.value.data, imageBlock.value.mime)
    return
  }
  resourceStore.openWithDirectContent(`${toolName} Result`, resultContent.value)
}

// Tool handler registry
const toolHandlers = {
  'Read': ReadToolHandler,
  'Write': WriteToolHandler,
  'Bash': BashToolHandler,
  'TodoWrite': TodoToolHandler,
  'Edit': EditToolHandler,
  'Grep': SearchToolHandler,
  'Glob': SearchToolHandler,
  'WebFetch': WebToolHandler,
  'WebSearch': WebToolHandler,
  'Agent': AgentToolHandler,
  'Task': AgentToolHandler,
  'TaskCreate': TaskCreateToolHandler,
  'TaskUpdate': TaskUpdateToolHandler,
  'TaskList': TaskListToolHandler,
  'TaskGet': TaskGetToolHandler,
  'NotebookEdit': NotebookEditToolHandler,
  'BashOutput': ShellToolHandler,
  'KillShell': ShellToolHandler,
  'SlashCommand': SlashCommandToolHandler,
  'Skill': SkillToolHandler,
  'ExitPlanMode': ExitPlanModeToolHandler,
  'AskUserQuestion': AskUserQuestionToolHandler,
  'mcp__legion__send_comm': SendCommToolHandler,
}

const toolHandlerComponent = computed(() => {
  return toolHandlers[props.toolCall.name] || BaseToolHandler
})

const isAskUserQuestion = computed(() => props.toolCall.name === 'AskUserQuestion')

// Permission details (post-decision display)
const hasPermissionInfo = computed(() => {
  return props.toolCall.permissionDecision != null && effectiveStatus.value !== 'permission_required'
})

const hasAppliedUpdates = computed(() => {
  return props.toolCall.appliedUpdates && props.toolCall.appliedUpdates.length > 0
})

const permissionStatusIcon = computed(() => {
  return props.toolCall.permissionDecision === 'allow' ? '✓' : '✗'
})

const permissionStatusColor = computed(() => {
  return props.toolCall.permissionDecision === 'allow' ? '#22c55e' : '#ef4444'
})

const permissionStatusLabel = computed(() => {
  return props.toolCall.permissionDecision === 'allow' ? 'Approved by user' : 'Denied by user'
})

const permissionDetailsClass = computed(() => {
  return props.toolCall.permissionDecision === 'allow' ? 'permission-approved' : 'permission-denied'
})

function formatSuggestion(suggestion) {
  if (suggestion.type === 'setMode') {
    return `Set mode: ${suggestion.mode}`
  } else if (suggestion.type === 'addRules' && suggestion.rules?.length) {
    const ruleStrs = suggestion.rules.map(r => r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName)
    return `Allow: ${ruleStrs.join(', ')}`
  } else if (suggestion.type === 'addDirectories' && suggestion.directories?.length) {
    return `Add directories: ${suggestion.directories.join(', ')}`
  }
  return JSON.stringify(suggestion)
}
</script>

<style scoped>
.timeline-detail {
  margin-top: 4px;
  padding: 0;
  font-size: 13px;
  max-height: 400px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* Banners */
.detail-banner {
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  font-size: 12px;
}

.detail-banner strong { display: block; margin-bottom: 2px; }
.detail-banner p { margin: 0; opacity: 0.8; }

.detail-banner-warning {
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.45);
  color: var(--bs-body-color);
}

.detail-banner-info {
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.4);
  color: var(--bs-body-color);
}

.detail-banner-error {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: var(--bs-body-color);
}

/* Permission Details (post-decision) */
.permission-details {
  padding: 5px 8px;
  border-radius: 4px;
  margin-bottom: 8px;
  font-size: 11px;
  border-left: 3px solid;
}

.permission-approved {
  background: rgba(34, 197, 94, 0.1);
  border-left-color: #22c55e;
}

.permission-denied {
  background: rgba(239, 68, 68, 0.1);
  border-left-color: #ef4444;
}

.permission-auto-approved {
  background: rgba(167, 139, 250, 0.1);
  border-left-color: #a78bfa;
}

.permission-status {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  color: var(--bs-body-color);
}

.permission-status-icon {
  font-weight: 700;
  font-size: 12px;
}

.permission-changes {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}

.permission-changes .changes-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--bs-secondary-color);
}

.permission-changes ul {
  margin: 2px 0 0;
  padding-left: 16px;
  font-size: 11px;
  color: var(--bs-body-color);
}

.permission-changes li {
  padding: 1px 0;
}

/* View Full Result bar */
.view-full-bar {
  text-align: right;
  padding: 4px 8px;
  border-top: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
}

.view-full-link {
  font-size: 11px;
  font-weight: 500;
  color: var(--bs-link-color);
  cursor: pointer;
  text-decoration: none;
}

.view-full-link:hover {
  text-decoration: underline;
}
</style>
