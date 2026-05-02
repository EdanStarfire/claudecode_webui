<template>
  <div class="lib-section">
    <SettingsToolbar title="Templates" />
    <div v-if="templateStore.loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="templateStore.error" class="section-error">{{ templateStore.error }}</div>
    <div v-else class="section-body">
      <div class="list-header">
        <span class="list-count">{{ templateStore.templateList.length }} template{{ templateStore.templateList.length === 1 ? '' : 's' }}</span>
        <div class="header-actions">
          <button class="btn-action btn-secondary-action" @click="openImport">Import</button>
          <button class="btn-action btn-primary-action" @click="openCreate">+ New Template</button>
        </div>
      </div>

      <div v-if="templateStore.templateList.length === 0" class="empty-state">
        No templates yet. Create your first template to define reusable session configurations.
      </div>

      <div
        v-for="template in templateStore.templateList"
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
        <div class="template-meta">
          <span class="meta-badge perm-badge">{{ template.permission_mode || 'default' }}</span>
          <span class="meta-tools">{{ template.allowed_tools?.length || 0 }} tool{{ (template.allowed_tools?.length || 0) === 1 ? '' : 's' }}</span>
          <span class="row-chevron" aria-hidden="true">›</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, watch } from 'vue'
import { useTemplateStore } from '@/stores/template'
import { useUIStore } from '@/stores/ui'
import SettingsToolbar from '../SettingsToolbar.vue'

const templateStore = useTemplateStore()
const uiStore = useUIStore()

function openCreate() {
  uiStore.showModal('configuration', { mode: 'create-template' })
}

function openEdit(template) {
  uiStore.showModal('configuration', { mode: 'edit-template', template })
}

function openImport() {
  // Open template-list mode which has the import button
  uiStore.showModal('template-management')
}

// Refetch when ConfigurationModal closes so the list stays in sync
watch(() => uiStore.currentModal, (modal, prev) => {
  if (!modal && prev?.name === 'configuration') {
    templateStore.fetchTemplates()
  }
})

onMounted(() => {
  templateStore.fetchTemplates()
})
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-status {
  padding: 20px;
  color: #94a3b8;
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
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.list-count {
  font-size: 13px;
  color: var(--bs-secondary-color);
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-action {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-secondary-action {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-secondary-action:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.btn-primary-action {
  background: #6366f1;
  border-color: #6366f1;
  color: #fff;
}

.btn-primary-action:hover {
  background: #818cf8;
  border-color: #818cf8;
}

.empty-state {
  padding: 32px 16px;
  text-align: center;
  color: var(--bs-secondary-color);
  font-size: 13px;
  background: var(--bs-tertiary-bg);
  border-radius: 8px;
  border: 1px dashed var(--bs-border-color);
}

.template-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--bs-border-color);
  margin-bottom: 8px;
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

.template-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.template-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.template-desc {
  font-size: 12px;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.template-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.meta-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 10px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  white-space: nowrap;
}

.meta-tools {
  font-size: 12px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  line-height: 1;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
