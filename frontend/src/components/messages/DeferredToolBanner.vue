<template>
  <div class="deferred-tool-banner">
    <div class="deferred-tool-banner-header">
      <span class="deferred-icon">&#x23F8;</span>
      <span class="deferred-label">Run stopped: tool <code>{{ deferredToolUse.name }}</code> deferred by hook</span>
      <button
        v-if="hasInput"
        class="btn btn-sm deferred-toggle"
        @click="expanded = !expanded"
        :aria-expanded="expanded"
        aria-label="Toggle deferred tool input"
      >{{ expanded ? '&#x25B2;' : '&#x25BC;' }}</button>
    </div>
    <div v-if="expanded && hasInput" class="deferred-tool-input">
      <pre class="deferred-input-pre">{{ formattedInput }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  deferredToolUse: {
    type: Object,
    required: true
  }
})

const expanded = ref(false)

const hasInput = computed(() =>
  props.deferredToolUse.input && Object.keys(props.deferredToolUse.input).length > 0
)

const formattedInput = computed(() =>
  JSON.stringify(props.deferredToolUse.input, null, 2)
)
</script>

<style scoped>
.deferred-tool-banner {
  margin: 4px 12px 8px;
  border: 1px solid var(--bs-warning-border-subtle, #ffc107);
  border-radius: 6px;
  background: var(--bs-warning-bg-subtle, rgba(255, 193, 7, 0.1));
  font-size: 0.875rem;
  overflow: hidden;
}

.deferred-tool-banner-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
}

.deferred-icon {
  font-size: 1em;
  color: var(--bs-warning, #ffc107);
  flex-shrink: 0;
}

.deferred-label {
  flex: 1;
  color: var(--bs-body-color);
}

.deferred-label code {
  font-size: 0.85em;
  background: var(--bs-code-bg, rgba(0,0,0,0.08));
  padding: 1px 4px;
  border-radius: 3px;
}

.deferred-toggle {
  padding: 0 6px;
  font-size: 0.7em;
  line-height: 1.6;
  color: var(--bs-secondary-color);
  border-color: var(--bs-border-color);
}

.deferred-tool-input {
  border-top: 1px solid var(--bs-warning-border-subtle, rgba(255, 193, 7, 0.3));
  padding: 8px 10px;
}

.deferred-input-pre {
  margin: 0;
  font-size: 0.8rem;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--bs-body-color);
  background: transparent;
}
</style>
