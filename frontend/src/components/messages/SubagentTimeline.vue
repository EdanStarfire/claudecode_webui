<template>
  <SubagentGutter
    :agentColor="agentColor"
    :needsAttention="hasOpenPermission"
    :tooltipLabel="tooltipLabel"
    :slotIndex="slotIndex"
    @toggle="expanded = !expanded"
  >
    <SubagentAnchorRow
      :anchorType="primaryAnchorType"
      :agentColor="agentColor"
      :description="description"
      :subagentType="subagentType"
      :timestamp="primaryTimestamp"
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
  </SubagentGutter>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import SubagentGutter from './SubagentGutter.vue'
import SubagentAnchorRow from './SubagentAnchorRow.vue'
import SubagentLegTranscript from './SubagentLegTranscript.vue'

// Issue #1746 (stage: subagents) / #1765: rewritten from a single tool_use_id-keyed
// collapsed card into a thin per-leg orchestrator. `launchToolCall` is the ONE Task/Agent
// tool_use that produced THIS specific leg (a resume is a brand new Task/Agent call with its
// own tool_use_id, so it gets rendered by its own separate SubagentTimeline instance at its
// own position in the message flow — never sharing identity/DOM with an earlier leg's card,
// which is what makes the #1765 eviction bug structurally impossible here).
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

const subagentType = computed(() => props.launchToolCall.input?.subagent_type || null)

const description = computed(() => {
  const fromLeg = leg.value?.description
  if (fromLeg) return fromLeg
  const desc = props.launchToolCall.input?.description
  if (desc) return desc
  const prompt = props.launchToolCall.input?.prompt
  return prompt || 'Subagent task'
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

// Issue #1746 (stage: subagents): "needs attention" resolved directly from already-available
// store data (no new backend surface needed) — any tool call whose parent_tool_use_id is THIS
// leg's own launch/resume tool_use_id and is currently awaiting a permission decision.
const hasOpenPermission = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return false
  const toolCalls = messageStore.toolCallsBySession.get(sessionId) || []
  return toolCalls.some(tc =>
    tc.parent_tool_use_id === props.launchToolCall.id &&
    getEffectiveStatusForTool(tc) === 'permission_required'
  )
})

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

const expanded = ref(false)

// Issue #1746 (stage: subagents) review fix: the permission-needed anchor is record-only
// (per spec §4.3, action buttons are stage 4's job) — but the actual Allow/Deny UI for the
// leg's blocked child tool lives inside SubagentLegTranscript's ActivityTimeline, which is
// collapsed by default. Auto-expand once a permission is actually open so that UI isn't
// hidden behind an extra click; leave the user's own toggle in control after that.
// immediate: true — a reload/cold-mount can land with a permission ALREADY open (not just a
// live transition to it), which a non-immediate watch would never observe as a "change".
watch(hasOpenPermission, (isOpen) => {
  if (isOpen) expanded.value = true
}, { immediate: true })

// Issue #1746 (stage: subagents): claim/release a shared, dynamic sticky-offset slot while
// this leg is the currently-active one — see message.js claimGutterSlot/releaseGutterSlot.
const slotIndex = ref(null)
function syncGutterSlot() {
  const id = taskId.value
  if (id && isRunning.value) {
    slotIndex.value = messageStore.claimGutterSlot(id)
  } else if (id) {
    messageStore.releaseGutterSlot(id)
    slotIndex.value = null
  }
}
watch([taskId, isRunning], syncGutterSlot, { immediate: true })
onUnmounted(() => {
  // Issue #1746 (stage: subagents) review fix: only release if THIS instance still believes
  // it holds the claim (slotIndex !== null). Two SubagentTimeline instances for the same
  // task_id can coexist (an earlier terminal leg's own component alongside a later resumed
  // leg's) — releasing unconditionally by task_id would let an unrelated, already-released
  // instance's unmount evict a sibling leg's still-active slot claim out from under it.
  if (taskId.value && slotIndex.value !== null) messageStore.releaseGutterSlot(taskId.value)
})
</script>
