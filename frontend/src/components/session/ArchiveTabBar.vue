<template>
  <ul class="nav nav-tabs archive-tab-bar">
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'messages' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'messages')"
      >Messages</button>
    </li>
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'proxy' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'proxy')"
      >
        Proxy Logs
        <span v-if="proxyCount > 0" class="count-badge">{{ proxyCount }}</span>
      </button>
    </li>
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'queue' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'queue')"
      >
        Queue
        <span v-if="queueCount > 0" class="count-badge">{{ queueCount }}</span>
      </button>
    </li>
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'attachments' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'attachments')"
      >
        Attachments
        <span v-if="attachmentsCount > 0" class="count-badge">{{ attachmentsCount }}</span>
      </button>
    </li>
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'history' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'history')"
      >
        History
        <span v-if="historyCount > 0" class="count-badge">{{ historyCount }}</span>
      </button>
    </li>
    <li class="nav-item">
      <button
        class="nav-link"
        :class="{ active: activeTab === 'schedules' }"
        @click="archiveTabsStore.setActiveTab(sessionId, 'schedules')"
      >
        Schedules
        <span v-if="schedulesCount > 0" class="count-badge">{{ schedulesCount }}</span>
      </button>
    </li>
  </ul>
</template>

<script setup>
import { computed } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const activeTab = computed(() => archiveTabsStore.activeTabForSession(props.sessionId))
const proxyCount = computed(() => archiveTabsStore.proxyLogsForSession(props.sessionId).length)
const queueCount = computed(() => archiveTabsStore.reconstructedQueueForSession(props.sessionId).length)
const attachmentsCount = computed(() => archiveTabsStore.attachmentsForSession(props.sessionId).length)
const historyCount = computed(() => archiveTabsStore.historyForSession(props.sessionId).length)
const schedulesCount = computed(() => archiveTabsStore.schedulesForSession(props.sessionId).schedules.length)
</script>

<style scoped>
.archive-tab-bar {
  flex-wrap: nowrap;
  overflow-x: auto;
  -ms-overflow-style: none;
  scrollbar-width: none;
  flex-shrink: 0;
}
.archive-tab-bar::-webkit-scrollbar {
  display: none;
}
.archive-tab-bar .nav-link {
  white-space: nowrap;
  font-size: 0.8125rem;
  padding: 0.35rem 0.75rem;
}
.count-badge {
  margin-left: 5px;
  font-size: 11px;
  background: var(--bs-secondary-bg);
  border-radius: 8px;
  padding: 1px 6px;
}
</style>
