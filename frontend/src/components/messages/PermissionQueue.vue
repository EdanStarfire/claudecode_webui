<template>
  <div v-if="permissions.length > 0" class="permission-queue">
    <!-- Desktop: corner card stack -->
    <div
      v-if="!uiStore.isMobile"
      class="perm-queue-panel"
      :style="{ bottom: `${bottomOffset}px` }"
    >
      <div class="perm-queue-header" @click="minimized && (minimized = false)">
        <span class="perm-queue-title">
          <span class="perm-queue-count-badge">{{ permissions.length }}</span>
          Permission{{ permissions.length === 1 ? '' : 's' }} needed
        </span>
        <button
          v-if="!minimized"
          type="button"
          class="perm-queue-minimize-btn"
          aria-label="Minimize permission queue"
          @click.stop="minimized = true"
        >&minus;</button>
      </div>
      <div v-if="!minimized" class="perm-queue-cards">
        <PermissionQueueCard
          v-for="perm in permissions"
          :key="perm.requestId"
          :perm="perm"
          :submitting="!!isSubmitting[perm.requestId]"
          @approve="approve(perm)"
          @deny="deny(perm)"
          @view="viewInContext(perm)"
        />
      </div>
    </div>

    <!-- Mobile: FAB + bottom sheet -->
    <template v-else>
      <button
        type="button"
        class="perm-fab"
        :style="{ bottom: `${bottomOffset + 12}px` }"
        aria-label="View pending permissions"
        @click="sheetOpen = true"
      >
        <span class="perm-fab-glyph">&#9888;</span>
        <span class="perm-fab-badge">{{ permissions.length }}</span>
      </button>
      <div v-if="sheetOpen" class="perm-sheet-scrim" @click="sheetOpen = false"></div>
      <div v-if="sheetOpen" class="perm-sheet">
        <div class="perm-sheet-header">
          <span class="perm-queue-title">
            <span class="perm-queue-count-badge">{{ permissions.length }}</span>
            Permission{{ permissions.length === 1 ? '' : 's' }} needed
          </span>
          <button type="button" class="perm-sheet-close" aria-label="Close" @click="sheetOpen = false">&times;</button>
        </div>
        <div class="perm-sheet-cards">
          <PermissionQueueCard
            v-for="perm in permissions"
            :key="perm.requestId"
            :perm="perm"
            :submitting="!!isSubmitting[perm.requestId]"
            @approve="approve(perm)"
            @deny="deny(perm)"
            @view="viewInContext(perm)"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { usePollingStore } from '@/stores/polling'
import { useUIStore } from '@/stores/ui'
import PermissionQueueCard from './PermissionQueueCard.vue'

// Issue #1746 (stage: permissions): the floating action surface for open permission requests —
// desktop corner card stack / mobile FAB+sheet, branched on uiStore.isMobile (same pattern
// ActivityTimeline.vue already uses for its own mobile/desktop split). Mounted once in
// SessionView.vue, same "mounted once, absolutely positioned overlay" pattern as
// SubagentGlobalGutter.vue — not per-message.
const props = defineProps({
  sessionId: {
    type: String,
    default: null
  },
  bottomOffset: {
    type: Number,
    default: 0
  },
  // Issue #1748 (stage: offset-model): MessageList's exposed instance (scrollToItemIndex,
  // resolveToolAnchorIndex, resolveSubagentPrimaryIndex) — forwarded down by SessionView.vue
  // since MessageList is a sibling, not an ancestor.
  virtualNav: {
    type: Object,
    default: null
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const wsStore = usePollingStore()
const uiStore = useUIStore()

const permissions = computed(() => messageStore.openPermissionsForSession(props.sessionId))

// Issue #1746 (stage: permissions) decision #3: session-mount-scoped, not persisted — resets
// to expanded whenever the queue re-appears. No outside-click/blur handler anywhere (US2) —
// only the explicit minimize button drives this.
const minimized = ref(false)
const sheetOpen = ref(false)
const isSubmitting = ref({})

async function approve(perm) {
  await resolve(perm, 'allow')
}

async function deny(perm) {
  await resolve(perm, 'deny')
}

// Issue #1746 (stage: permissions) US5: same two calls PermissionPrompt.vue makes
// (handlePermissionResponse + sendPermissionResponse) — no divergent code path.
async function resolve(perm, decision) {
  if (isSubmitting.value[perm.requestId]) return
  isSubmitting.value = { ...isSubmitting.value, [perm.requestId]: true }
  try {
    const sessionId = sessionStore.currentSessionId
    if (sessionId) {
      messageStore.handlePermissionResponse(sessionId, {
        request_id: perm.requestId,
        decision,
        reasoning: decision === 'allow' ? 'User allowed permission' : 'User denied permission',
        applied_updates: []
      })
    }
    await wsStore.sendPermissionResponse(perm.requestId, decision, false, null, null)
  } catch (error) {
    console.error('Failed to send permission response from queue:', error)
  } finally {
    const next = { ...isSubmitting.value }
    delete next[perm.requestId]
    isSubmitting.value = next
  }
}

// Issue #1746 (stage: permissions) US4: expand-then-scroll ordering — the leg transcript must
// actually be in the DOM before scrollIntoView runs, so the expand is awaited (nextTick) before
// scrolling (matches the desktop mockup's viewInContext() ordering).
// Issue #1748 (stage: offset-model): routed through MessageList's shared virtual-navigation
// helper (plan §5.5) when available, since the target row may be anywhere in a long session's
// history and isn't guaranteed to already be in the DOM. Falls back to the pre-#1748 direct
// document.getElementById lookup when virtualNav isn't wired (e.g. archived/deleted-agent views
// don't render a live MessageList) or the index couldn't be resolved — correct whenever the
// target happens to already be mounted, which Stage 1's overscan=count guarantees anyway.
async function viewInContext(perm) {
  if (perm.isSubagent) {
    messageStore.setLegExpanded(perm.taskId, perm.legIndex, true)
    await nextTick()
    const index = props.virtualNav?.resolveSubagentPrimaryIndex(perm.taskId, perm.legIndex)
    const mounted = index != null ? await props.virtualNav.scrollToItemIndex(index, { align: 'center' }) : false
    // index == null covers both "no virtualNav" and "virtualNav present but couldn't resolve an
    // index" (e.g. an orphaned permission tool — indexMaps doesn't scan orphanedPermissionTools)
    // — both cases must fall back to the direct DOM lookup, not just the former.
    if (mounted || index == null) {
      requestAnimationFrame(() => {
        document.getElementById(`subagent-anchor-primary-${perm.taskId}-${perm.legIndex}`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
    }
  } else {
    const index = props.virtualNav?.resolveToolAnchorIndex(perm.toolCall.id)
    const mounted = index != null ? await props.virtualNav.scrollToItemIndex(index, { align: 'center' }) : false
    if (mounted || index == null) {
      document.getElementById(`tool-anchor-${perm.toolCall.id}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
  if (uiStore.isMobile) sheetOpen.value = false
}
</script>

<style scoped>
.perm-queue-panel {
  position: absolute;
  right: 16px;
  z-index: 20;
  width: 300px;
  max-width: calc(100% - 32px);
  max-height: 60vh;
  display: flex;
  flex-direction: column;
  background: var(--perm-queue-card-bg);
  border: 1px solid var(--perm-queue-card-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}

.perm-queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  flex-shrink: 0;
}

.perm-queue-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-body-color);
}

.perm-queue-count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--perm-queue-minimize-bg);
  color: var(--bs-emphasis-color);
  font-size: 11px;
  font-weight: 700;
}

.perm-queue-minimize-btn {
  border: none;
  background: transparent;
  color: var(--bs-secondary-color);
  font-size: 16px;
  line-height: 1;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  cursor: pointer;
}
.perm-queue-minimize-btn:hover {
  background: var(--perm-queue-minimize-bg);
}

.perm-queue-cards {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 8px 8px;
  overflow-y: auto;
}

/* Mobile FAB */
.perm-fab {
  position: fixed;
  right: 16px;
  z-index: 30;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: none;
  background: var(--perm-queue-card-border);
  color: #1a1a1a;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.perm-fab-glyph {
  font-size: 18px;
}

.perm-fab-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--bs-danger, #ef4444);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.perm-sheet-scrim {
  position: fixed;
  inset: 0;
  background: var(--perm-queue-scrim);
  z-index: 25;
}

.perm-sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background: var(--perm-queue-card-bg);
  border-top: 1px solid var(--perm-queue-card-border);
  border-radius: 12px 12px 0 0;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.25);
}

.perm-sheet-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  flex-shrink: 0;
}

.perm-sheet-close {
  border: none;
  background: transparent;
  color: var(--bs-secondary-color);
  font-size: 20px;
  line-height: 1;
  width: 28px;
  height: 28px;
  cursor: pointer;
}

.perm-sheet-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 12px 16px;
  overflow-y: auto;
}
</style>
