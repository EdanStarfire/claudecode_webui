<template>
  <div class="audit-view d-flex flex-column h-100">
    <!-- Header -->
    <div class="audit-header d-flex align-items-center px-3 py-2 border-bottom">
      <h6 class="mb-0 fw-bold">Audit</h6>
      <!-- Tabs -->
      <ul class="nav nav-tabs border-0 ms-3">
        <li class="nav-item">
          <button
            class="nav-link py-1 px-3"
            :class="{ active: auditStore.activeTab === 'turns' }"
            @click="switchTab('turns')"
          >Turns</button>
        </li>
        <li class="nav-item">
          <button
            class="nav-link py-1 px-3"
            :class="{ active: auditStore.activeTab === 'stream' }"
            @click="switchTab('stream')"
          >Stream</button>
        </li>
      </ul>
    </div>

    <!-- Filter bar (shared) -->
    <AuditFilterBar @refresh="onRefresh" />

    <!-- Tab content -->
    <div class="flex-grow-1 overflow-auto">
      <AuditTurnsTab v-if="auditStore.activeTab === 'turns'" />
      <AuditStreamTab v-else />
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useAuditStore } from '../../stores/audit.js'
import AuditFilterBar from './AuditFilterBar.vue'
import AuditTurnsTab from './AuditTurnsTab.vue'
import AuditStreamTab from './AuditStreamTab.vue'

const auditStore = useAuditStore()

onMounted(() => {
  auditStore.fetchTurns()
})

function switchTab(tab) {
  auditStore.setActiveTab(tab)
  if (tab === 'turns') {
    auditStore.fetchTurns()
  }
}

function onRefresh() {
  if (auditStore.activeTab === 'turns') {
    auditStore.fetchTurns()
  } else {
    auditStore.fetchEvents()
  }
}
</script>

<style scoped>
.audit-view { background: var(--bs-body-bg); }
.audit-header { background: var(--bs-tertiary-bg); }
.nav-link { font-size: 0.875rem; }
</style>
