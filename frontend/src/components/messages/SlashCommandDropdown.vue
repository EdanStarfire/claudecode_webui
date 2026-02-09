<template>
  <ul
    v-if="commands.length > 0"
    ref="listRef"
    class="slash-command-dropdown"
    role="listbox"
    aria-label="Slash commands"
  >
    <li
      v-for="(command, index) in commands"
      :id="`slash-cmd-${index}`"
      :key="command"
      role="option"
      class="slash-command-item"
      :class="{ active: index === selectedIndex }"
      :aria-selected="index === selectedIndex"
      @mousedown.prevent="$emit('select', command)"
      @mouseenter="$emit('highlight', index)"
    >
      /{{ command }}
    </li>
  </ul>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  commands: {
    type: Array,
    required: true
  },
  selectedIndex: {
    type: Number,
    default: 0
  }
})

defineEmits(['select', 'highlight'])

const listRef = ref(null)

// Scroll selected item into view when navigating with keyboard
watch(() => props.selectedIndex, (index) => {
  if (!listRef.value) return
  const item = listRef.value.children[index]
  if (item) {
    item.scrollIntoView({ block: 'nearest' })
  }
})
</script>

<style scoped>
.slash-command-dropdown {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 180px;
  overflow-y: auto;
  margin: 0;
  padding: 4px 0;
  list-style: none;
  background: var(--bs-body-bg, #fff);
  border: 1px solid var(--bs-border-color, #dee2e6);
  border-radius: 6px;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.15);
  z-index: 1050;
}

.slash-command-item {
  padding: 3px 10px;
  min-height: 28px;
  display: flex;
  align-items: center;
  cursor: pointer;
  font-family: var(--bs-font-monospace);
  font-size: 0.82rem;
  color: var(--bs-body-color);
  transition: background-color 0.1s;
}

.slash-command-item:hover,
.slash-command-item.active {
  background-color: var(--bs-primary);
  color: #fff;
}

@media (max-width: 767.98px) {
  .slash-command-dropdown {
    max-height: 220px;
  }

  .slash-command-item {
    min-height: 36px;
    font-size: 0.88rem;
  }
}
</style>
