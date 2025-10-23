<template>
  <header class="bg-white border-bottom shadow-sm d-flex justify-content-between align-items-center px-3 py-2">
    <div class="d-flex align-items-center gap-2">
      <button
        id="sidebar-toggle-btn"
        class="btn btn-sm btn-outline-secondary"
        title="Toggle sidebar"
        @click="toggleSidebar"
      >
        <span>☰</span>
      </button>
      <h1 class="mb-0 fs-4">Claude Code WebUI</h1>
    </div>
    <div class="d-flex align-items-center gap-2">
      <ConnectionIndicator />
    </div>
  </header>
</template>

<script setup>
import { useUIStore } from '@/stores/ui'
import ConnectionIndicator from './ConnectionIndicator.vue'

const uiStore = useUIStore()

function toggleSidebar() {
  uiStore.toggleSidebar()
}
</script>
