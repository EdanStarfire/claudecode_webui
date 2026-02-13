<template>
  <div
    class="peek-card"
    :style="peekStyle"
    :title="childSession?.name || 'Child agent'"
    @click.stop="$emit('click')"
  >
    <div class="peek-dot" :class="statusClass"></div>
    <div v-if="hasAlert" class="peek-alert" :class="alertClass"></div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  sessionId: { type: String, required: true },
  index: { type: Number, default: 0 }
})

defineEmits(['click'])

const sessionStore = useSessionStore()

const childSession = computed(() => sessionStore.getSession(props.sessionId))

const peekStyle = computed(() => ({
  right: `${-16 - (props.index * 14)}px`
}))

const statusClass = computed(() => {
  if (!childSession.value) return 'dot-none'
  const state = childSession.value.state
  if (state === 'active' && childSession.value.is_processing) return 'dot-active'
  if (state === 'active') return 'dot-idle'
  if (state === 'paused') return 'dot-waiting'
  if (state === 'error') return 'dot-error'
  return 'dot-none'
})

const hasAlert = computed(() => {
  if (!childSession.value) return false
  return childSession.value.has_pending_permission || childSession.value.state === 'error'
})

const alertClass = computed(() => {
  if (!childSession.value) return ''
  if (childSession.value.state === 'error') return 'alert-error'
  return 'alert-permission'
})
</script>

<style scoped>
.peek-card {
  position: absolute;
  top: 0;
  width: 14px;
  height: 34px;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 0;
  transition: all 0.15s;
}

.peek-card:hover {
  border-color: #93c5fd;
  background: #eff6ff;
}

.peek-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.dot-active { background: #8b5cf6; }
.dot-idle { background: #22c55e; }
.dot-waiting { background: #f97316; }
.dot-error { background: #ef4444; }
.dot-none { background: #e2e8f0; }

.peek-alert {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1px solid white;
}

.alert-permission { background: #f59e0b; }
.alert-error { background: #ef4444; }
</style>
