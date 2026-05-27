<template>
  <div class="stacked-chip" :style="stackStyle">
    <!-- Parent Chip -->
    <div class="parent-wrapper" :class="{ 'has-children': childIds.length > 0 }">
      <AgentChip
        :session="session"
        :isActive="isActive"
        :isParentOfActive="isParentOfActive"
        @select="$emit('select', session.session_id)"
        @dblclick.native="toggleExpand"
      />

      <!-- Peek Cards (collapsed state) -->
      <template v-if="!isExpanded && childIds.length > 0">
        <PeekCard
          v-for="(item, index) in visiblePeek"
          :key="item.sessionId"
          :sessionId="item.sessionId"
          :index="index"
          :depth="item.depth"
          :cap="cap"
          @click="handlePeekClick"
        />
        <PeekSentinelCard
          v-if="showSentinel"
          :index="visiblePeek.length"
          :cap="cap"
          :hiddenSessionIds="hiddenPeek.map(d => d.sessionId)"
          @click="toggleExpand"
        />
      </template>

      <!-- Stack Count Badge -->
      <div
        v-if="childIds.length > 0"
        class="stack-count"
        role="button"
        :aria-label="`${childCountLabel}, click to open project overview`"
        @click.stop="navigateToProject"
        :title="childCountLabel"
      >
        {{ childIds.length }}
      </div>
    </div>

    <!-- Expanded Chain -->
    <template v-if="isExpanded">
      <template v-for="childId in childIds" :key="childId">
        <ChipConnector :depth="depth" :maxDepth="treeMaxDepth" />
        <!-- Recursive StackedChip if child also has children -->
        <StackedChip
          v-if="getChildSession(childId) && hasGrandchildren(childId)"
          :session="getChildSession(childId)"
          :isActive="childId === currentSessionId || isDescendantActive(childId)"
          :depth="depth + 1"
          :maxDepth="treeMaxDepth"
          @select="$emit('select', $event)"
        />
        <AgentChip
          v-else-if="getChildSession(childId)"
          :session="getChildSession(childId)"
          :isActive="childId === currentSessionId"
          variant="child"
          @select="$emit('select', childId)"
        />
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { compareAgents } from '@/utils/agentSort'
import AgentChip from './AgentChip.vue'
import PeekCard from './PeekCard.vue'
import PeekSentinelCard from './PeekSentinelCard.vue'
import ChipConnector from './ChipConnector.vue'

defineOptions({ name: 'StackedChip' })

const props = defineProps({
  session: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  depth: { type: Number, default: 1 },
  maxDepth: { type: Number, default: 0 } // 0 = auto-compute from tree
})

const emit = defineEmits(['select'])

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const currentSessionId = computed(() => sessionStore.currentSessionId)

const childIds = computed(() => {
  const rawIds = (props.session.child_minion_ids || []).filter(id => sessionStore.getSession(id))
  const mode = uiStore.agentSort
  return rawIds.sort((a, b) => {
    const sa = sessionStore.getSession(a)
    const sb = sessionStore.getSession(b)
    return compareAgents(mode, sa, sb, {
      nameOf: s => s.name,
      orderOf: s => s.order,
      idOf: s => s.session_id
    })
  })
})

const childCountLabel = computed(() => {
  const n = childIds.value.length
  return `${n} child agent${n !== 1 ? 's' : ''}`
})

// Compute max depth of the subtree rooted at this session
function computeTreeDepth(session, level) {
  if (!session?.child_minion_ids?.length) return level
  let max = level
  for (const cid of session.child_minion_ids) {
    const child = sessionStore.getSession(cid)
    if (child) {
      max = Math.max(max, computeTreeDepth(child, level + 1))
    }
  }
  return max
}

// If maxDepth wasn't provided (root StackedChip), compute it from the tree
const treeMaxDepth = computed(() => {
  if (props.maxDepth > 0) return props.maxDepth
  // Depth from this node's children downward (this node is level 0, children are level 1+)
  return computeTreeDepth(props.session, 0)
})

// Check if the active session is anywhere in this stack's descendant tree
const hasActiveDescendant = computed(() => {
  if (!currentSessionId.value) return false
  function checkDescendants(ids) {
    if (!ids) return false
    for (const cid of ids) {
      if (cid === currentSessionId.value) return true
      const child = sessionStore.getSession(cid)
      if (child?.child_minion_ids && checkDescendants(child.child_minion_ids)) return true
    }
    return false
  }
  return checkDescendants(childIds.value)
})

const isParentOfActive = computed(() => {
  if (!currentSessionId.value) return false
  if (props.session.session_id === currentSessionId.value) return false
  return hasActiveDescendant.value
})

const isExpanded = computed(() => {
  return uiStore.expandedStacks.has(props.session.session_id) || hasActiveDescendant.value
})

// Flat DFS traversal of all descendants for peek stack
// Returns [{sessionId, depth}, ...] in parent→child order
// depth 1 = direct children, depth 2 = grandchildren, etc.
const allDescendantsForPeek = computed(() => {
  const result = []
  function traverse(ids, depth) {
    for (const id of ids) {
      result.push({ sessionId: id, depth })
      const child = sessionStore.getSession(id)
      if (child?.child_minion_ids?.length) {
        traverse(child.child_minion_ids, depth + 1)
      }
    }
  }
  traverse(childIds.value, 1)
  return result
})

const cap = computed(() => uiStore.maxPeekCards)
const visiblePeek = computed(() => allDescendantsForPeek.value.slice(0, cap.value))
const hiddenPeek = computed(() => allDescendantsForPeek.value.slice(cap.value))
const showSentinel = computed(() => hiddenPeek.value.length > 0)

const SENTINEL_EXTRA = 110

const stackStyle = computed(() => {
  const base = { '--parent-z': cap.value + 10 }
  if (isExpanded.value || allDescendantsForPeek.value.length === 0) return base
  const renderedCount = visiblePeek.value.length
  const sentinelPx = showSentinel.value ? SENTINEL_EXTRA : 0
  return { ...base, marginRight: `${renderedCount * 22 + sentinelPx}px` }
})

function getChildSession(childId) {
  return sessionStore.getSession(childId)
}

function hasGrandchildren(childId) {
  const child = sessionStore.getSession(childId)
  return child?.child_minion_ids?.length > 0
}

function isDescendantActive(childId) {
  const child = sessionStore.getSession(childId)
  if (!child?.child_minion_ids) return false
  return child.child_minion_ids.includes(currentSessionId.value)
}

function toggleExpand() {
  uiStore.toggleStack(props.session.session_id)
}

function navigateToProject() {
  const projectId = props.session.project_id
  if (!projectId) return
  uiStore.setBrowsingProject(projectId)
  sessionStore.clearSessionSelection()
  router.push(`/project/${projectId}`)
}

function handlePeekClick(childSessionId) {
  if (!isExpanded.value) {
    uiStore.toggleStack(props.session.session_id)
  }
  emit('select', childSessionId)
}
</script>

<style scoped>
.stacked-chip {
  display: flex;
  align-items: center;
  position: relative;
  flex-shrink: 0;
}

.parent-wrapper {
  position: relative;
}

.parent-wrapper > :deep(.agent-chip) {
  position: relative;
  z-index: var(--parent-z, 25);
}

.stack-count {
  position: absolute;
  bottom: -3px;
  right: -3px;
  min-width: 15px;
  height: 15px;
  border-radius: 8px;
  background: #475569;
  color: white;
  font-size: 8px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
  cursor: pointer;
  z-index: calc(var(--parent-z, 25) + 1);
}

.stack-count:hover {
  background: #334155;
}
</style>
