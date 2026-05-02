<template>
  <div class="settings-takeover" :style="modeTintVars">
    <div class="settings-coming-soon">
      <p class="settings-coming-soon__label">Settings — coming soon</p>
      <p class="settings-coming-soon__route">{{ $route.path }}</p>
    </div>
    <DirtyGuardModal
      :visible="settingsStore.pendingNavigation !== null"
      @apply="onGuardApply"
      @discard="onGuardDiscard"
      @cancel="onGuardCancel"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import DirtyGuardModal from './DirtyGuardModal.vue'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()

const modeTintVars = computed(() => {
  const p = route.path
  if (p.startsWith('/settings/session/')) {
    return {
      '--mode-tint': '#1f6feb18',
      '--mode-border': '#1f6feb44',
      '--mode-fg': '#58a6ff',
    }
  }
  if (p.startsWith('/settings/template/')) {
    return {
      '--mode-tint': '#d2992215',
      '--mode-border': '#d2992244',
      '--mode-fg': '#d29922',
    }
  }
  if (p.startsWith('/settings/profile/')) {
    return {
      '--mode-tint': '#3fb95018',
      '--mode-border': '#3fb95044',
      '--mode-fg': '#3fb950',
    }
  }
  return {}
})

function onGuardApply() {
  // Phase 1+ will save the active area; for now just navigate
  const dest = settingsStore.confirmNavigation('apply')
  if (dest) router.push(dest)
}

function onGuardDiscard() {
  const dest = settingsStore.confirmNavigation('discard')
  if (dest) router.push(dest)
}

function onGuardCancel() {
  settingsStore.confirmNavigation('cancel')
}
</script>

<style scoped>
.settings-takeover {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  background: var(--mode-tint, transparent);
  border-top: 2px solid var(--mode-border, transparent);
}

.settings-coming-soon {
  text-align: center;
}

.settings-coming-soon__label {
  font-size: 18px;
  font-weight: 600;
  color: #94a3b8;
  margin: 0 0 8px;
}

.settings-coming-soon__route {
  font-size: 12px;
  color: #475569;
  font-family: monospace;
  margin: 0;
}
</style>
