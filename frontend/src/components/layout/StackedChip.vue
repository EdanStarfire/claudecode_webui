<template>
  <div class="stacked-chip" :style="stackStyle">
    <!-- Parent Chip -->
    <div class="parent-wrapper" :class="{ 'has-children': childIds.length > 0 }">
      <AgentChip
        :session="session"
        :isActive="isActive"
        @select="$emit('select', session.session_id)"
        @dblclick.native="toggleExpand"
      />

      <!-- Peek Cards (collapsed state) -->
      <template v-if="!isExpanded && childIds.length > 0">
        <PeekCard
          v-for="(childId, index) in visiblePeekIds"
          :key="childId"
          :sessionId="childId"
          :index="index"
          @click="handlePeekClick"
        />
      </template>

      <!-- Stack Count Badge -->
      <div
        v-if="childIds.length > 0"
        class="stack-count"
        @click.stop="toggleExpand"
        :title="`${childIds.length} child agent${childIds.length !== 1 ? 's' : ''}`"
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
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import AgentChip from './AgentChip.vue'
import PeekCard from './PeekCard.vue'
import ChipConnector from './ChipConnector.vue'

defineOptions({ name: 'StackedChip' })

const props = defineProps({
  session: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  depth: { type: Number, default: 1 },
  maxDepth: { type: Number, default: 0 } // 0 = auto-compute from tree
})

defineEmits(['select'])

const sessionStore = useSessionStore()
const uiStore = useUIStore()

const currentSessionId = computed(() => sessionStore.currentSessionId)

const childIds = computed(() => {
  return props.session.child_minion_ids || []
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

const isExpanded = computed(() => {
  return uiStore.expandedStacks.has(props.session.session_id) || hasActiveDescendant.value
})

const visiblePeekIds = computed(() => {
  return childIds.value
})

// Margin-right for peek card protrusion
const stackStyle = computed(() => {
  if (isExpanded.value || childIds.value.length === 0) return {}
  const peekCount = childIds.value.length
  return {
    marginRight: `${peekCount * 22}px`
  }
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

function handlePeekClick() {
  if (!isExpanded.value) {
    uiStore.toggleStack(props.session.session_id)
  }
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
  z-index: 25;
}

.stack-count {
  position: absolute;
  bottom: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: #475569;
  color: white;
  font-size: 9px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
  cursor: pointer;
  z-index: 26;
  border: 1px solid white;
}

.stack-count:hover {
  background: #334155;
}
</style>
