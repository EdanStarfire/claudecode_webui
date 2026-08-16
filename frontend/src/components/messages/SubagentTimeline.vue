<template>
  <div class="subagent-timeline">
    <SubagentAnchorRow
      :id="primaryAnchorId"
      :anchorType="primaryAnchorType"
      :agentColor="agentColor"
      :description="description"
      :subagentType="subagentType"
      :timestamp="primaryTimestamp"
      clickable
      :title="tooltipLabel"
      @click="toggleExpanded"
    />
    <SubagentAnchorRow
      v-if="hasOpenPermission"
      anchorType="permission-needed"
      :agentColor="agentColor"
      description="Waiting on a permission decision"
      :timestamp="null"
    />
    <SubagentAnchorRow
      v-else-if="leg && leg.status !== 'running'"
      anchorType="completed"
      :agentColor="agentColor"
      :description="terminalDescription"
      :statusText="leg.status"
      :timestamp="leg.ended_at"
    />
    <SubagentAnchorRow
      v-else-if="isOrphaned"
      anchorType="completed"
      :agentColor="agentColor"
      description="Interrupted before this leg completed"
      statusText="stopped"
      :timestamp="null"
    />

    <SubagentLegTranscript
      v-if="expanded"
      :taskId="taskId"
      :legIndex="legIndex"
      :legToolCall="props.launchToolCall"
      :isRunning="isRunning"
    />
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import SubagentAnchorRow from './SubagentAnchorRow.vue'
import SubagentLegTranscript from './SubagentLegTranscript.vue'

// Issue #1746 (stage: subagents) / #1765: rewritten from a single tool_use_id-keyed
// collapsed card into a thin per-leg orchestrator. `launchToolCall` is the ONE Task/Agent
// tool_use that produced THIS specific leg (a resume is a brand new Task/Agent call with its
// own tool_use_id, so it gets rendered by its own separate SubagentTimeline instance at its
// own position in the message flow — never sharing identity/DOM with an earlier leg's card,
// which is what makes the #1765 eviction bug structurally impossible here).
//
// Issue #1746 follow-up: this component no longer owns any gutter/sticky-chip rendering —
// that's SubagentGlobalGutter.vue's job now, mounted ONCE in MessageList.vue as a persistent
// overlay outside the message flow (a subagent can run for many unrelated turns; a chip scoped
// to this one card's own small DOM footprint can't stay visible for that whole span). This
// component just renders the inline anchor rows/transcript and exposes a stable DOM id
// (primaryAnchorId) on its own primary row so the global gutter can measure its position.
const props = defineProps({
  launchToolCall: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// task_id isn't known until this leg's own task_started frame has arrived (SDK id-stability
// point) — until then, render a bare pending launch anchor keyed on the tool_use_id itself.
const taskId = computed(() => messageStore.getTaskIdForLaunchToolUse(props.launchToolCall.id))
const legEntry = computed(() => taskId.value ? messageStore.getTaskLegEntry(taskId.value) : null)
const legIndex = computed(() => {
  if (!legEntry.value) return -1
  return legEntry.value.legs.findIndex(l => l.tool_use_id === props.launchToolCall.id)
})
const leg = computed(() => legIndex.value >= 0 ? legEntry.value.legs[legIndex.value] : null)

// Issue #1746 follow-up: stable id the global gutter measures against (see
// SubagentGlobalGutter.vue) — only assignable once task_id is known, matching when the leg
// itself becomes eligible for a gutter lane.
const primaryAnchorId = computed(() =>
  taskId.value ? `subagent-anchor-primary-${taskId.value}-${legIndex.value}` : undefined
)

const subagentType = computed(() => props.launchToolCall.input?.subagent_type || null)

// Issue #1746 (stage: subagents) follow-up: a resume anchor's own launching tool call is
// `SendMessage(to: "<agent name>", summary, message)`, not a Task/Agent call — it carries no
// description/prompt fields, only summary/message. Fall back to those so a resumed leg's
// anchor still shows something meaningful instead of the bare "Subagent task" default.
const description = computed(() => {
  const fromLeg = leg.value?.description
  if (fromLeg) return fromLeg
  const input = props.launchToolCall.input || {}
  return input.description || input.prompt || input.summary || input.message || 'Subagent task'
})

const terminalDescription = computed(() => {
  const type = subagentType.value ? `${subagentType.value} ` : ''
  return `${type}agent ${leg.value?.status || 'finished'}`
})

const primaryAnchorType = computed(() => (legIndex.value > 0 ? 'resumed' : 'launch'))
const primaryTimestamp = computed(() => leg.value?.started_at ?? props.launchToolCall.timestamp)

// Issue #1746 (stage: subagents) review fix: a leg's `status` only ever transitions away
// from 'running' via a task_notification/task_updated terminal frame — but a session
// interrupt/restart/termination orphans the launching tool_use without emitting one (the
// backend's TaskLegRegistry has no hook into that path either). Without this check, an
// interrupted subagent shows as running forever and its gutter slot is never released.
const isOrphaned = computed(() => getEffectiveStatusForTool(props.launchToolCall) === 'orphaned')

const isRunning = computed(() => {
  if (isOrphaned.value) return false
  if (leg.value) return leg.value.status === 'running'
  // task_started hasn't arrived yet — fall back to the launching tool_use's own dispatch state.
  return ['pending', 'executing'].includes(getEffectiveStatusForTool(props.launchToolCall))
})

const hasOpenPermission = computed(() =>
  messageStore.hasOpenPermissionForTask(sessionStore.currentSessionId, taskId.value)
)

// Issue #1746 (stage: subagents): color keys on task_id once known (stable per agent
// instance across its legs), falling back to subagent_type/tool_use_id before then.
const agentColorSlug = computed(() =>
  slugifyAgentName(taskId.value || subagentType.value || props.launchToolCall.id)
)
const agentColor = computed(() => getAgentColor(agentColorSlug.value))

const tooltipLabel = computed(() => {
  const type = subagentType.value ? `${subagentType.value}: ` : ''
  return `${type}${description.value}`
})

// Issue #1746 follow-up: expand state lives in the shared store (not a local ref) — the
// global gutter chip, rendered in a completely different part of the DOM, needs to toggle the
// SAME state as clicking this row directly.
const expanded = computed(() => messageStore.isLegExpanded(taskId.value, legIndex.value))
function toggleExpanded() {
  messageStore.toggleLegExpanded(taskId.value, legIndex.value)
}

// Issue #1746 (stage: subagents) review fix: the permission-needed anchor is record-only
// (per spec §4.3, action buttons are stage 4's job) — but the actual Allow/Deny UI for the
// leg's blocked child tool lives inside SubagentLegTranscript's ActivityTimeline, which is
// collapsed by default. Auto-expand once a permission is actually open so that UI isn't
// hidden behind an extra click; leave the user's own toggle in control after that.
// immediate: true — a reload/cold-mount can land with a permission ALREADY open (not just a
// live transition to it), which a non-immediate watch would never observe as a "change".
watch(hasOpenPermission, (isOpen) => {
  if (isOpen && taskId.value) messageStore.setLegExpanded(taskId.value, legIndex.value, true)
}, { immediate: true })
</script>

<style scoped>
.subagent-timeline {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
