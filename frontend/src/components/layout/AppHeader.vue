<template>
  <header class="border-bottom shadow-sm d-flex justify-content-between align-items-center px-3 py-2" :class="uiStore.isRedBackground ? 'theme-red-header' : 'bg-white'">
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
      <button
        class="btn btn-sm"
        :class="uiStore.isRedBackground ? 'btn-outline-danger' : 'btn-outline-secondary'"
        title="Toggle background color"
        @click="uiStore.toggleBackgroundColor()"
      >
        <span
          class="color-swatch"
          :style="{ backgroundColor: uiStore.isRedBackground ? '#ffebee' : '#f5f5f5' }"
        ></span>
      </button>
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

<style scoped>
.color-swatch {
  display: inline-block;
  width: 1rem;
  height: 1rem;
  border-radius: 0.25rem;
  border: 1px solid #6c757d;
}
</style>
