<template>
  <div class="header-row1">
    <h1 class="header-title">Claude Code WebUI</h1>
    <div class="header-right">
      <div class="header-indicator" :class="uiConnected ? 'connected' : 'disconnected'">
        <span class="indicator-dot"></span>
        {{ uiConnected ? 'Connected' : 'Disconnected' }}
      </div>
      <button
        class="header-btn"
        :class="{ 'theme-active': uiStore.isRedBackground }"
        @click="uiStore.toggleBackgroundColor()"
        title="Toggle instance color"
      >
        <span class="color-swatch" :style="{ backgroundColor: uiStore.isRedBackground ? '#ef4444' : '#94a3b8' }"></span>
      </button>
      <button class="header-btn" @click="uiStore.showRestartModal()" title="Restart server">
        ↻
      </button>
      <button class="header-btn" @click="uiStore.showModal('configuration')" title="Settings">
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

.header-btn.theme-active {
  border-color: #ef4444;
}

.color-swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  border: 1px solid rgba(255, 255, 255, 0.3);
}

.panel-toggle-btn {
  display: none;
}

.panel-toggle-btn.panel-open {
  border-color: #3b82f6;
  color: #93c5fd;
}

@media (max-width: 1024px) {
  .panel-toggle-btn {
    display: flex;
  }
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
