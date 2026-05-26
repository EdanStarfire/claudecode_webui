<template>
  <div class="links-panel">
    <div v-if="links.length === 0" class="empty-placeholder">
      <span>Links registered by the agent will appear here</span>
    </div>
    <ul v-else class="link-list">
      <li
        v-for="link in links"
        :key="link.label"
        class="link-item"
      >
        <a
          :href="link.url"
          :title="link.url"
          target="_blank"
          rel="noopener noreferrer"
          class="link-anchor"
        >{{ link.label }}</a>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useLinksStore } from '@/stores/links'

const linksStore = useLinksStore()
const links = computed(() => linksStore.currentLinks)
</script>

<style scoped>
.links-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.empty-placeholder {
  text-align: center;
  padding: 24px 16px;
  color: var(--bs-secondary-color);
  font-size: 12px;
  font-style: italic;
}

.link-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.link-item {
  padding: 4px 12px;
  border-bottom: 1px solid var(--bs-border-color-translucent);
}

.link-item:last-child {
  border-bottom: none;
}

.link-anchor {
  display: block;
  font-size: 12px;
  color: var(--bs-link-color);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.link-anchor:hover {
  text-decoration: underline;
  color: var(--bs-link-hover-color);
}
</style>
