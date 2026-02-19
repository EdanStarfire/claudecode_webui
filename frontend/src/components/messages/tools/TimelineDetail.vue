<template>
  <div class="timeline-detail">
    <!-- Orphaned Banner -->
    <div v-if="isOrphaned" class="detail-banner detail-banner-warning">
      <span class="banner-icon">⏹️</span>
      <div>
        <strong>Tool Execution Cancelled</strong>
        <p>{{ orphanedInfo?.message || 'Session was terminated' }}</p>
      </div>
    </div>

    <!-- Permission Details (shown after permission resolved) -->
    <div v-if="hasPermissionInfo" class="permission-details" :class="permissionDetailsClass">
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

  </div>
</template>

<script setup>
import { ref, computed, toRef } from 'vue'
import { useToolStatus } from '@/composables/useToolStatus'
import BaseToolHandler from '@/components/tools/BaseToolHandler.vue'
import ReadToolHandler from '@/components/tools/ReadToolHandler.vue'
import WriteToolHandler from '@/components/tools/WriteToolHandler.vue'
import BashToolHandler from '@/components/tools/BashToolHandler.vue'
import TodoToolHandler from '@/components/tools/TodoToolHandler.vue'
import EditToolHandler from '@/components/tools/EditToolHandler.vue'
import SearchToolHandler from '@/components/tools/SearchToolHandler.vue'
import WebToolHandler from '@/components/tools/WebToolHandler.vue'
import TaskToolHandler from '@/components/tools/TaskToolHandler.vue'
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

const props = defineProps({
  toolCall: { type: Object, required: true }
})

// Use shared composable for status computation
const { effectiveStatus, isOrphaned, orphanedInfo } = useToolStatus(toRef(props, 'toolCall'))

// Local state
const handlerRef = ref(null)

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
  'Task': TaskToolHandler,
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
  background: #fef3c7;
  border: 1px solid #fbbf24;
  color: #92400e;
}

.detail-banner-info {
  background: #dbeafe;
  border: 1px solid #60a5fa;
  color: #1e40af;
}

.detail-banner-error {
  background: #fee2e2;
  border: 1px solid #f87171;
  color: #991b1b;
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
  background: #f0fdf4;
  border-left-color: #22c55e;
}

.permission-denied {
  background: #fef2f2;
  border-left-color: #ef4444;
}

.permission-status {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  color: #334155;
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
  color: #64748b;
}

.permission-changes ul {
  margin: 2px 0 0;
  padding-left: 16px;
  font-size: 11px;
  color: #475569;
}

.permission-changes li {
  padding: 1px 0;
}
</style>
