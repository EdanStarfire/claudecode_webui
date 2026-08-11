<template>
  <span class="position-relative d-inline-block" ref="trayRef">
    <button
      type="button"
      class="header-btn tray-btn"
      title="Notifications"
      aria-label="Notification tray"
      @click.stop="toggleOpen"
    >
      🔔
      <span v-if="unreadCount > 0" class="tray-badge">{{ badgeLabel }}</span>
    </button>

    <div
      v-if="open"
      class="tray-popover card shadow"
      role="dialog"
      aria-label="Notification tray"
    >
      <div class="card-header d-flex justify-content-between align-items-center py-2 px-3">
        <span class="fw-semibold small">Notifications</span>
        <button
          v-if="entries.length > 0"
          type="button"
          class="btn btn-sm btn-link text-decoration-none p-0 small"
          @click.stop="trayStore.clearAll()"
        >Clear all</button>
      </div>
      <div class="tray-list">
        <div v-if="entries.length === 0" class="text-muted small text-center py-4">
          No notifications
        </div>
        <div
          v-for="entry in entries"
          :key="entry.id"
          class="tray-entry"
          @click="onEntryClick(entry)"
        >
          <div class="tray-entry-body">
            <div class="tray-entry-title">{{ entry.title }}</div>
            <div v-if="entry.body" class="tray-entry-detail">{{ entry.body }}</div>
            <div class="tray-entry-meta">
              <span v-if="entry.sessionName">{{ entry.sessionName }} &middot; </span>{{ getRelativeTime(entry.timestamp) }}
            </div>
          </div>
          <button
            type="button"
            class="tray-entry-dismiss"
            title="Dismiss"
            aria-label="Dismiss notification"
            @click.stop="trayStore.dismissEntry(entry.id)"
          >&times;</button>
        </div>
      </div>
    </div>
  </span>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationTrayStore } from '@/stores/notificationTray'
import { getRelativeTime } from '@/utils/time'

const router = useRouter()
const trayStore = useNotificationTrayStore()

const trayRef = ref(null)
const open = ref(false)

const entries = computed(() => trayStore.entries)
const unreadCount = computed(() => trayStore.unreadCount)
const badgeLabel = computed(() => unreadCount.value > 99 ? '99+' : String(unreadCount.value))

function toggleOpen() {
  open.value = !open.value
}

function onEntryClick(entry) {
  const sessionId = trayStore.markReadAndNavigate(entry.id)
  if (sessionId) {
    router.push(`/session/${sessionId}`)
  }
  open.value = false
}

function handleClickOutside(event) {
  if (open.value && trayRef.value && !trayRef.value.contains(event.target)) {
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
.tray-btn {
  position: relative;
  font-size: 13px;
}

.tray-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 9px;
  line-height: 15px;
  text-align: center;
  font-weight: 600;
}

.tray-popover {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 1060;
  width: 320px;
  max-width: 90vw;
}

.tray-list {
  max-height: 360px;
  overflow-y: auto;
}

.tray-entry {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--bs-border-color);
  cursor: pointer;
}

.tray-entry:hover {
  background: var(--bs-secondary-bg);
}

.tray-entry-body {
  flex: 1;
  min-width: 0;
}

.tray-entry-title {
  font-size: 13px;
  font-weight: 600;
}

.tray-entry-detail {
  font-size: 12px;
  color: var(--bs-secondary-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tray-entry-meta {
  font-size: 11px;
  color: var(--bs-secondary-color);
  margin-top: 2px;
}

.tray-entry-dismiss {
  flex-shrink: 0;
  background: none;
  border: none;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  color: var(--bs-secondary-color);
  padding: 0 2px;
}

.tray-entry-dismiss:hover {
  color: var(--bs-body-color);
}
</style>
