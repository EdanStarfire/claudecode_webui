<template>
  <div v-if="tools && tools.length > 0" class="mcp-tool-list-wrapper">
    <button
      class="btn btn-link btn-sm p-0 text-muted"
      @click="expanded = !expanded"
    >
      {{ expanded ? 'Hide' : 'Show' }} {{ tools.length }} tool{{ tools.length !== 1 ? 's' : '' }}
    </button>
    <div v-if="expanded" class="tool-list mt-1">
      <div
        v-for="tool in tools"
        :key="tool.name"
        class="tool-item small py-1 px-2"
      >
        <code>{{ tool.name }}</code>
        <span v-if="tool.description" class="text-muted ms-2">{{ tool.description }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  tools: {
    type: Array,
    default: () => [],
  },
})

const expanded = ref(false)
</script>

<style scoped>
.tool-list {
  max-height: 200px;
  overflow-y: auto;
}

.tool-item {
  border-radius: 0.25rem;
  background: var(--bs-tertiary-bg);
  color: var(--bs-body-color);
}

.tool-item code {
  color: var(--bs-code-color);
  background: transparent;
}

.tool-item + .tool-item {
  margin-top: 2px;
}
</style>
