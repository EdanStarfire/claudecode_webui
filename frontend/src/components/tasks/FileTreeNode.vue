<template>
  <div class="file-tree-node">
    <!-- Node Row -->
    <div
      class="node-row"
      :class="{ 'is-folder': isFolder, 'is-file': !isFolder }"
      :style="{ paddingLeft: `${depth * 16}px` }"
      @click="handleClick"
    >
      <!-- Chevron for folders -->
      <span v-if="isFolder" class="node-chevron">
        <svg
          width="12" height="12" viewBox="0 0 12 12"
          :class="{ 'chevron-expanded': expanded }"
        >
          <path d="M4 2 L8 6 L4 10" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
      </span>
      <span v-else class="node-spacer"></span>

      <!-- Icon -->
      <span class="node-icon">
        <svg v-if="isFolder && expanded" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M1 3.5A1.5 1.5 0 012.5 2h3.879a1.5 1.5 0 011.06.44l1.122 1.12A1.5 1.5 0 009.62 4H13.5A1.5 1.5 0 0115 5.5v1H2.5A1.5 1.5 0 001 5V3.5z"/>
          <path d="M1.06 7.5A1 1 0 012.04 6.5h11.92a1 1 0 01.98 1.2l-1.25 6a1 1 0 01-.98.8H3.28a1 1 0 01-.98-.8L1.06 7.7v-.2z"/>
        </svg>
        <svg v-else-if="isFolder" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M2.5 2A1.5 1.5 0 001 3.5v2A1.5 1.5 0 002.5 7h3.879a1.5 1.5 0 001.06-.44l1.122-1.12A1.5 1.5 0 019.62 5H13.5A1.5 1.5 0 0115 6.5v6a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 011 12.5v-9A1.5 1.5 0 012.5 2z"/>
        </svg>
        <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M4 1.5A1.5 1.5 0 015.5 0h4.879a1.5 1.5 0 011.06.44l2.122 2.12A1.5 1.5 0 0114 3.62V14.5a1.5 1.5 0 01-1.5 1.5h-7A1.5 1.5 0 014 14.5V1.5zM5.5 1a.5.5 0 00-.5.5v13a.5.5 0 00.5.5h7a.5.5 0 00.5-.5V3.621a.5.5 0 00-.146-.354L10.732 1.146A.5.5 0 0010.379 1H5.5z"/>
        </svg>
      </span>

      <!-- Name -->
      <span class="node-name" :title="node.path">{{ node.name }}</span>

      <!-- Loading indicator -->
      <span v-if="isFolder && loading" class="node-loading">
        <div class="spinner-border spinner-border-sm" role="status"></div>
      </span>
    </div>

    <!-- Children (recursive) -->
    <div v-if="isFolder && expanded" class="node-children">
      <FileTreeNode
        v-for="child in children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :session-id="sessionId"
      />
      <div v-if="!loading && children.length === 0" class="empty-folder" :style="{ paddingLeft: `${(depth + 1) * 16}px` }">
        <span class="text-muted small">Empty folder</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useFileBrowserStore } from '@/stores/filebrowser'

const props = defineProps({
  node: {
    type: Object,
    required: true,
  },
  depth: {
    type: Number,
    default: 0,
  },
  sessionId: {
    type: String,
    required: true,
  },
})

const fileBrowserStore = useFileBrowserStore()

const isFolder = computed(() => props.node.type === 'folder')
const expanded = computed(() => fileBrowserStore.isExpanded(props.sessionId, props.node.path))
const loading = computed(() => fileBrowserStore.isLoading(props.sessionId, props.node.path))

const children = computed(() => {
  return fileBrowserStore.getEntries(props.sessionId, props.node.path)
})

function handleClick() {
  if (isFolder.value) {
    fileBrowserStore.toggleFolder(props.sessionId, props.node.path)
  } else {
    fileBrowserStore.openFile(props.node.path)
  }
}
</script>

<style scoped>
.node-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  cursor: pointer;
  user-select: none;
  font-size: 0.82rem;
  border-radius: 3px;
  min-height: 26px;
}

.node-row:hover {
  background-color: rgba(0, 0, 0, 0.05);
}

.node-row.is-file:hover {
  color: #0d6efd;
}

.node-chevron {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  flex-shrink: 0;
  color: #6c757d;
}

.node-chevron svg {
  transition: transform 0.15s ease;
}

.node-chevron .chevron-expanded {
  transform: rotate(90deg);
}

.node-spacer {
  width: 14px;
  flex-shrink: 0;
}

.node-icon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  color: #6c757d;
}

.is-folder .node-icon {
  color: #e8a317;
}

.node-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.node-loading {
  flex-shrink: 0;
  margin-left: 4px;
}

.node-loading .spinner-border {
  width: 12px;
  height: 12px;
  border-width: 1.5px;
  color: #6c757d;
}

.empty-folder {
  padding: 2px 8px;
  font-style: italic;
}
</style>
