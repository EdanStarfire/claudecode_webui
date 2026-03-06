<template>
  <div class="truncation-banner" v-if="!dismissed">
    <span class="truncation-icon">&#x26A0;</span>
    <span class="truncation-text">Response truncated (token limit reached)</span>
    <button class="truncation-dismiss" @click="dismissed = true" aria-label="Dismiss">&times;</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const dismissed = ref(false)
</script>

<style scoped>
.truncation-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 16px 8px 16px;
  padding: 6px 12px;
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 6px;
  font-size: 0.82rem;
  color: #6d4c00;
}

.truncation-icon {
  font-size: 0.9rem;
  flex-shrink: 0;
}

.truncation-text {
  flex: 1;
}

.truncation-dismiss {
  background: none;
  border: none;
  color: #6d4c00;
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  opacity: 0.6;
}

.truncation-dismiss:hover {
  opacity: 1;
}
</style>
