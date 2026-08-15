<template>
  <span class="position-relative d-inline-block" ref="menuRef">
    <button
      type="button"
      class="btn btn-sm btn-link session-actions-toggle"
      title="Session actions"
      aria-label="Session actions"
      @click.stop="toggleOpen"
    >
      <svg class="session-actions-icon" width="14" height="14" viewBox="0 0 4 16" aria-hidden="true">
        <circle cx="2" cy="2" r="2" fill="currentColor" />
        <circle cx="2" cy="8" r="2" fill="currentColor" />
        <circle cx="2" cy="14" r="2" fill="currentColor" />
      </svg>
    </button>

    <div
      v-if="open"
      class="session-actions-popover card shadow"
      role="menu"
      aria-label="Session actions"
      @click.stop
    >
      <button
        v-if="canMarkRead"
        type="button"
        class="session-actions-item"
        role="menuitem"
        :disabled="liveSession?.is_processing"
        @click="runAction(handleMarkRead)"
      >
        <i class="bi bi-envelope-open me-2"></i>Mark Read
      </button>
      <button
        v-if="canMarkUnread"
        type="button"
        class="session-actions-item"
        role="menuitem"
        :disabled="liveSession?.is_processing"
        @click="runAction(handleMarkUnread)"
      >
        <i class="bi bi-envelope-exclamation me-2"></i>Mark Unread
      </button>
      <button
        type="button"
        class="session-actions-item"
        role="menuitem"
        @click="runAction(showEditModal)"
      >
        <i class="bi bi-gear me-2"></i>Edit Session
      </button>
      <button
        type="button"
        class="session-actions-item"
        role="menuitem"
        @click="runAction(showManageModal)"
      >
        <i class="bi bi-tools me-2"></i>Manage Session
      </button>

      <template v-if="projectId">
        <div class="session-actions-divider"></div>
        <div class="session-actions-heading">Move to</div>
        <button
          v-for="option in moveToOptions"
          :key="option.groupId ?? 'unassigned'"
          type="button"
          class="session-actions-item"
          role="menuitem"
          @click="runAction(() => moveToGroup(option.groupId))"
        >
          {{ option.name }}
        </button>
      </template>
    </div>
  </span>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useProjectStore } from '@/stores/project'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  },
  projectId: {
    type: String,
    default: null
  }
})

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const projectStore = useProjectStore()

const menuRef = ref(null)
const open = ref(false)

const liveSession = computed(() => sessionStore.getSession(props.sessionId))

// Issue #1597/#1646: same visibility rules as the old node-action buttons
const isUnreviewed = computed(() => sessionStore.isUnreviewed(props.sessionId))
const canMarkUnread = computed(() => !!liveSession.value?.last_completion_at && !isUnreviewed.value)
const canMarkRead = computed(() => !!liveSession.value?.last_completion_at && isUnreviewed.value)

const project = computed(() => props.projectId ? projectStore.getProject(props.projectId) : null)

const currentGroupId = computed(() =>
  project.value?.kanban_group_assignments?.[props.sessionId] || 'unassigned'
)

// "Move to" lists Unassigned plus the project's kanban groups, minus the session's current group
const moveToOptions = computed(() => {
  const options = []
  if (currentGroupId.value !== 'unassigned') {
    options.push({ groupId: null, name: 'Unassigned' })
  }
  for (const group of project.value?.kanban_groups || []) {
    if (group.group_id !== currentGroupId.value) {
      options.push({ groupId: group.group_id, name: group.name })
    }
  }
  return options
})

function toggleOpen() {
  open.value = !open.value
}

function runAction(fn) {
  open.value = false
  fn()
}

function showEditModal() {
  router.push(`/settings/session/${props.sessionId}/general`)
}

function showManageModal() {
  const session = sessionStore.sessions.get(props.sessionId)
  if (session) {
    uiStore.showModal('manage-session', { session })
  }
}

async function handleMarkUnread() {
  try {
    await sessionStore.markUnread(props.sessionId)
  } catch (e) {
    console.error('Failed to mark unread:', e)
  }
}

async function handleMarkRead() {
  try {
    await sessionStore.markRead(props.sessionId)
  } catch (e) {
    console.error('Failed to mark read:', e)
  }
}

async function moveToGroup(groupId) {
  if (!props.projectId) return
  try {
    await projectStore.assignSessionKanbanGroup(props.projectId, props.sessionId, groupId)
  } catch (e) {
    console.error('Failed to move session to kanban group:', e)
  }
}

function handleClickOutside(event) {
  if (open.value && menuRef.value && !menuRef.value.contains(event.target)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.session-actions-toggle {
  line-height: 1;
  color: var(--bs-secondary-color);
  border-radius: 4px;
  padding: 0.25rem;
}

.session-actions-toggle:hover,
.session-actions-toggle:focus-visible {
  color: var(--bs-body-color);
  background-color: var(--bs-secondary-bg);
}

.session-actions-icon {
  flex-shrink: 0;
  color: inherit;
}

.session-actions-popover {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 1060;
  width: 200px;
  padding: 0.25rem 0;
}

.session-actions-item {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 0.4rem 0.75rem;
  background: none;
  border: none;
  text-align: left;
  font-size: 0.85rem;
  color: var(--bs-body-color);
  cursor: pointer;
}

.session-actions-item:hover:not(:disabled) {
  background-color: var(--bs-secondary-bg);
}

.session-actions-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.session-actions-divider {
  border-top: 1px solid var(--bs-border-color);
  margin: 0.25rem 0;
}

.session-actions-heading {
  padding: 0.2rem 0.75rem;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--bs-secondary-color);
}
</style>
