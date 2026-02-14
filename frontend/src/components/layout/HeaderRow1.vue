<template>
  <div class="header-row1">
    <h1 class="header-title">Claude Code WebUI</h1>
    <div class="header-right">
      <div class="header-indicator" :class="uiConnected ? 'connected' : 'disconnected'">
        <span class="indicator-dot"></span>
        {{ uiConnected ? 'Connected' : 'Disconnected' }}
      </div>
      <button
        class="header-btn theme-toggle-btn"
        :class="{ 'theme-active': uiStore.isRedBackground }"
        @click="uiStore.toggleBackgroundColor()"
        :title="uiStore.isRedBackground ? 'Switch to light mode' : 'Switch to dark mode'"
      >
        <svg v-if="!uiStore.isRedBackground" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="5"/>
          <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
      </button>
      <button class="header-btn" @click="uiStore.showRestartModal()" title="Restart server">
        ↻
      </button>
      <button class="header-btn" disabled title="Settings (coming soon)" style="opacity: 0.4; cursor: default;">
        ⚙
      </button>
      <button
        class="header-btn panel-toggle-btn"
        @click="uiStore.toggleRightPanel()"
        title="Toggle right panel"
        :class="{ 'panel-open': uiStore.rightPanelVisible }"
      >
        ☰
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useWebSocketStore } from '@/stores/websocket'

const uiStore = useUIStore()
const wsStore = useWebSocketStore()

const uiConnected = computed(() => wsStore.uiConnected)
</script>

<style scoped>
.header-row1 {
  height: 42px;
  background: #1e293b;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  flex-shrink: 0;
  z-index: 100;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  color: #f1f5f9;
  margin: 0;
  letter-spacing: 0.3px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #94a3b8;
}

.header-indicator.connected {
  color: #94a3b8;
}

.header-indicator.disconnected {
  color: #ef4444;
}

.indicator-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
}

.header-indicator.connected .indicator-dot {
  background: #22c55e;
}

.header-indicator.disconnected .indicator-dot {
  background: #ef4444;
  animation: pulse-error 1.5s infinite;
}

.header-btn {
  background: none;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 14px;
  padding: 4px 8px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-btn:hover {
  background: #334155;
  border-color: #475569;
}

.theme-toggle-btn svg {
  display: block;
}

.header-btn.theme-active {
  border-color: #f59e0b;
  color: #fbbf24;
}

.panel-toggle-btn.panel-open {
  border-color: #3b82f6;
  color: #93c5fd;
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
