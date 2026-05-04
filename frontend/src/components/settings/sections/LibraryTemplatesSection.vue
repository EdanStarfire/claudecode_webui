<template>
  <div class="lib-section">
    <SettingsToolbar title="Templates">
      <template #actions>
        <button class="toolbar-btn" @click="openImport">Import</button>
      </template>
    </SettingsToolbar>

    <div v-if="templateStore.loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="templateStore.error" class="section-error">{{ templateStore.error }}</div>

    <div v-else class="section-body">
      <!-- Custom templates -->
      <div class="category-block">
        <div class="category-header">
          <span class="category-label">Custom</span>
          <button
            class="category-add-btn"
            title="New custom template"
            @click="quickCreate"
          >+</button>
        </div>

        <div v-if="customTemplates.length === 0" class="empty-state">
          No custom templates yet. Click + to create one.
        </div>

        <div
          v-for="template in customTemplates"
          :key="template.template_id"
          class="template-row"
          role="button"
          tabindex="0"
          @click="openEdit(template)"
          @keydown.enter.prevent="openEdit(template)"
          @keydown.space.prevent="openEdit(template)"
        >
          <div class="template-info">
            <span class="template-name">{{ template.name }}</span>
            <span v-if="template.description" class="template-desc">{{ template.description }}</span>
          </div>
          <span class="row-chevron" aria-hidden="true">›</span>
        </div>
      </div>

      <!-- Default templates -->
      <div v-if="defaultTemplates.length > 0" class="category-block">
        <div class="category-header">
          <span class="category-label">Default</span>
        </div>

        <div
          v-for="template in defaultTemplates"
          :key="template.template_id"
          class="template-row template-row--default"
          role="button"
          tabindex="0"
          @click="openEdit(template)"
          @keydown.enter.prevent="openEdit(template)"
          @keydown.space.prevent="openEdit(template)"
        >
          <div class="template-info">
            <span class="template-name">{{ template.name }}</span>
            <span v-if="template.description" class="template-desc">{{ template.description }}</span>
          </div>
          <span class="row-chevron" aria-hidden="true">›</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTemplateStore } from '@/stores/template'
import { useUIStore } from '@/stores/ui'
import SettingsToolbar from '../SettingsToolbar.vue'

const router = useRouter()
const templateStore = useTemplateStore()
const uiStore = useUIStore()

const customTemplates = computed(() =>
  templateStore.templateList
    .filter(t => !t.is_default)
    .sort((a, b) => a.name.localeCompare(b.name))
)

const defaultTemplates = computed(() =>
  templateStore.templateList
    .filter(t => t.is_default)
    .sort((a, b) => a.name.localeCompare(b.name))
)

function openEdit(template) {
  router.push(`/settings/template/${template.template_id}/general`)
}

function openImport() {
  uiStore.showModal('template-management')
}

function quickCreate() {
  router.push('/settings/template/__new__/general')
}

onMounted(() => templateStore.fetchTemplates())
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.toolbar-btn {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s;
}
.toolbar-btn:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.section-status {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.status-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

.section-error {
  padding: 16px 20px;
  color: #f87171;
  font-size: 13px;
  background: rgba(248, 113, 113, 0.08);
  border-bottom: 1px solid rgba(248, 113, 113, 0.2);
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Category ─────────────────────────────── */
.category-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 4px;
  border-bottom: 1px solid var(--bs-border-color);
}

.category-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-secondary-color);
}

.category-add-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.category-add-btn:hover:not(:disabled) {
  background: #6366f1;
  border-color: #6366f1;
  color: #fff;
}

.category-add-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ── Rows ─────────────────────────────────── */
.empty-state {
  padding: 20px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

.template-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 7px;
  border: 1px solid var(--bs-border-color);
  cursor: pointer;
  background: var(--bs-body-bg);
  transition: background 0.12s, border-color 0.12s;
}

.template-row:hover {
  background: var(--bs-tertiary-bg);
  border-color: #6366f1;
}

.template-row:focus-visible {
  outline: 2px solid #6366f1;
  outline-offset: 1px;
}

.template-row--default:hover {
  border-color: var(--bs-border-color);
  background: var(--bs-secondary-bg);
}

.template-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.template-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.template-desc {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  flex-shrink: 0;
  line-height: 1;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
