<template>
  <div v-if="hasRestartRequired" class="restart-banner alert alert-warning" role="alert" data-testid="restart-required-banner">
    <div class="restart-banner-text">
      <strong>Restart required.</strong>
      <span> A saved change has not taken effect on the running session:</span>
      <ul class="restart-reasons">
        <li v-for="reason in reasons" :key="reason">{{ reason }}</li>
      </ul>
    </div>
    <button type="button" class="btn btn-sm btn-warning restart-banner-btn" @click="openRestart">
      Restart…
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useRestartRequired } from '@/composables/useRestartRequired'

const route = useRoute()
const sessionId = computed(() =>
  route.path.startsWith('/settings/session/') && route.params.sessionId !== '__new__'
    ? route.params.sessionId
    : ''
)
const { reasons, hasRestartRequired, openRestart } = useRestartRequired(sessionId)
</script>

<style scoped>
.restart-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 0;
  padding: 8px 20px;
  border-radius: 0;
  border-width: 0 0 1px;
  font-size: 12.5px;
  flex-shrink: 0;
}
.restart-reasons { margin: 4px 0 0; padding-left: 18px; }
.restart-banner-btn { flex-shrink: 0; white-space: nowrap; }
</style>
