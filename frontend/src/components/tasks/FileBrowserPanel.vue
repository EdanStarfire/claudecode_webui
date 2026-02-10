<template>
  <div class="file-browser-panel">
    <!-- Header -->
    <div class="fb-header">
      <div class="fb-path" :title="rootPath">
        {{ displayPath }}
      </div>
      <div class="fb-actions">
        <!-- Hidden files toggle -->
        <button
          class="fb-btn"
          :class="{ active: fileBrowserStore.showHidden }"
          @click="fileBrowserStore.toggleHidden(sessionId)"
          title="Toggle hidden files"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path v-if="fileBrowserStore.showHidden" d="M8 2C4.318 2 1.128 4.38.096 7.753a.5.5 0 000 .494C1.128 11.62 4.318 14 8 14s6.872-2.38 7.904-5.753a.5.5 0 000-.494C14.872 4.38 11.682 2 8 2zm0 10a4 4 0 110-8 4 4 0 010 8zm0-6.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z"/>
            <path v-else d="M13.359 11.238l1.276 1.276a.5.5 0 01-.707.707l-1.414-1.414A8.96 8.96 0 018 14C4.318 14 1.128 11.62.096 8.247a.5.5 0 010-.494A9.007 9.007 0 012.78 4.9L1.365 3.486a.5.5 0 11.707-.707l11.287 11.287v.172zM5.9 7.02l4.08 4.08A2.5 2.5 0 015.9 7.02zM8 2c3.682 0 6.872 2.38 7.904 5.753a.5.5 0 010 .494 8.94 8.94 0 01-1.55 2.612l-1.42-1.42A6.96 6.96 0 0014.87 8 7.018 7.018 0 008 3c-.964 0-1.9.194-2.781.564L3.81 2.155A8.96 8.96 0 018 2z"/>
          </svg>
        </button>
        <!-- Refresh button -->
        <button
          class="fb-btn"
          @click="handleRefresh"
          :disabled="refreshing"
          title="Refresh"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" :class="{ 'spin': refreshing }">
            <path d="M11.534 7h3.932a.25.25 0 01.192.41l-1.966 2.36a.25.25 0 01-.384 0l-1.966-2.36a.25.25 0 01.192-.41zm-7.068 2H.534a.25.25 0 01-.192-.41l1.966-2.36a.25.25 0 01.384 0l1.966 2.36a.25.25 0 01-.192.41z"/>
            <path fill-rule="evenodd" d="M8 3a5 5 0 11-4.546 2.914.5.5 0 00-.908-.418A6 6 0 108 2v1z"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Tree Content -->
    <div class="fb-tree" v-if="rootPath">
      <!-- Loading state for initial load -->
      <div v-if="initialLoading" class="fb-loading">
        <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
        <span>Loading files...</span>
      </div>

      <!-- Tree entries -->
      <div v-else-if="rootEntries.length > 0" class="fb-tree-content">
        <FileTreeNode
          v-for="entry in rootEntries"
          :key="entry.path"
          :node="entry"
          :depth="0"
          :session-id="sessionId"
        />
      </div>

      <!-- Empty state -->
      <div v-else class="fb-empty">
        <span class="text-muted small">No files found</span>
      </div>
    </div>

    <!-- No working directory -->
    <div v-else class="fb-no-dir">
      <span class="text-muted small">No working directory configured</span>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, ref } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import { useFileBrowserStore } from '@/stores/filebrowser'
import FileTreeNode from './FileTreeNode.vue'

const sessionStore = useSessionStore()
const projectStore = useProjectStore()
const fileBrowserStore = useFileBrowserStore()

const refreshing = ref(false)

const sessionId = computed(() => sessionStore.currentSessionId)

const currentSession = computed(() => sessionStore.currentSession)

const rootPath = computed(() => {
  const session = currentSession.value
  if (session?.working_directory) return session.working_directory

  // Fall back to project working_directory
  if (session?.project_id) {
    const project = projectStore.projects.get(session.project_id)
    if (project?.working_directory) return project.working_directory
  }

  return null
})

const displayPath = computed(() => {
  if (!rootPath.value) return 'No directory'
  // Show just the last 2-3 path segments for brevity
  const parts = rootPath.value.split('/')
  if (parts.length > 3) {
    return '.../' + parts.slice(-2).join('/')
  }
  return rootPath.value
})

const rootEntries = computed(() => {
  if (!sessionId.value || !rootPath.value) return []
  return fileBrowserStore.getEntries(sessionId.value, rootPath.value)
})

const initialLoading = computed(() => {
  if (!sessionId.value || !rootPath.value) return false
  return fileBrowserStore.isLoading(sessionId.value, rootPath.value) && rootEntries.value.length === 0
})

// Load root directory when session changes or root path changes
watch([sessionId, rootPath], async ([newSessionId, newRootPath]) => {
  if (newSessionId && newRootPath) {
    await fileBrowserStore.loadDirectory(newSessionId, newRootPath)
  }
}, { immediate: true })

async function handleRefresh() {
  if (!sessionId.value) return
  refreshing.value = true
  try {
    await fileBrowserStore.refreshAll(sessionId.value)
  } finally {
    refreshing.value = false
  }
}
</script>

<style scoped>
.file-browser-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.fb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-bottom: 1px solid #dee2e6;
  background: #fff;
  flex-shrink: 0;
  gap: 8px;
}

.fb-path {
  font-size: 0.75rem;
  color: #6c757d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1;
}

.fb-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.fb-btn {
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 3px 5px;
  cursor: pointer;
  color: #6c757d;
  display: flex;
  align-items: center;
  justify-content: center;
}

.fb-btn:hover {
  background: #e9ecef;
  color: #495057;
}

.fb-btn.active {
  color: #0d6efd;
  background: rgba(13, 110, 253, 0.08);
}

.fb-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fb-tree {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 4px 0;
}

.fb-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: #6c757d;
  font-size: 0.82rem;
  justify-content: center;
}

.fb-empty,
.fb-no-dir {
  padding: 24px 16px;
  text-align: center;
}

.spin {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
