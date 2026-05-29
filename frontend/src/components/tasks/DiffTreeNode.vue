<template>
  <div>
    <!-- Folder node -->
    <div
      v-if="node.type === 'folder'"
      class="file-item d-flex align-items-center gap-2 py-1 border-bottom folder-row"
      :style="{ paddingLeft: (depth * 16 + 12) + 'px', paddingRight: '12px' }"
      @click="$emit('toggleFolder', node.path)"
    >
      <svg
        class="chevron-icon flex-shrink-0"
        :class="{ expanded: expandedFolders.has(node.path) }"
        width="10" height="10" viewBox="0 0 12 12"
      >
        <path d="M4.5 2L8.5 6L4.5 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
      </svg>
      <span class="file-path text-truncate flex-grow-1" :title="node.name">{{ node.name }}/</span>
      <span v-if="showStats && (node.insertions > 0 || node.deletions > 0)" class="file-stats small text-nowrap">
        <span v-if="node.insertions > 0" class="text-success">+{{ node.insertions }}</span>
        <span v-if="node.insertions > 0 && node.deletions > 0" class="text-muted">/</span>
        <span v-if="node.deletions > 0" class="text-danger">-{{ node.deletions }}</span>
      </span>
    </div>

    <!-- Recursive children when folder is expanded -->
    <template v-if="node.type === 'folder' && expandedFolders.has(node.path)">
      <DiffTreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :file-ref="fileRef"
        :expanded-folders="expandedFolders"
        :show-stats="showStats"
        :depth="depth + 1"
        @open-file="(p, r) => $emit('openFile', p, r)"
        @toggle-folder="(p) => $emit('toggleFolder', p)"
      />
    </template>

    <!-- File leaf -->
    <div
      v-if="node.type === 'file'"
      class="file-item d-flex align-items-center gap-2 py-1 border-bottom"
      :style="{ paddingLeft: (depth * 16 + 12) + 'px', paddingRight: '12px' }"
      @click="$emit('openFile', node.path, fileRef)"
    >
      <span class="status-icon flex-shrink-0" :class="'status-' + node.status">
        {{ statusIcon(node.status) }}
      </span>
      <span class="file-path text-truncate flex-grow-1" :title="node.path">{{ node.name }}</span>
      <template v-if="showStats">
        <span v-if="!node.is_binary" class="file-stats small text-nowrap">
          <span v-if="node.insertions > 0" class="text-success">+{{ node.insertions }}</span>
          <span v-if="node.insertions > 0 && node.deletions > 0" class="text-muted">/</span>
          <span v-if="node.deletions > 0" class="text-danger">-{{ node.deletions }}</span>
        </span>
        <span v-else class="file-stats small text-muted">binary</span>
      </template>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'DiffTreeNode' })

defineProps({
  node: { type: Object, required: true },
  fileRef: { default: null },
  expandedFolders: { type: Object, required: true },
  showStats: { type: Boolean, default: true },
  depth: { type: Number, default: 0 }
})

defineEmits(['openFile', 'toggleFolder'])

function statusIcon(status) {
  switch (status) {
    case 'added': return '+'
    case 'deleted': return '−'
    case 'renamed': return 'R'
    default: return 'M'
  }
}
</script>

<style scoped>
.file-item {
  cursor: pointer;
  font-size: 0.82rem;
}

.file-item:hover {
  background-color: var(--bs-secondary-bg);
}

.folder-row {
  color: var(--bs-secondary-color);
}

.folder-row:hover {
  color: var(--bs-body-color);
}

.status-icon {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-weight: 700;
  font-size: 0.75rem;
  width: 16px;
  text-align: center;
}

.status-added { color: var(--bs-success-text-emphasis, #198754); }
.status-deleted { color: var(--bs-danger-text-emphasis, #dc3545); }
.status-modified { color: var(--bs-warning-text-emphasis, #fd7e14); }
.status-renamed { color: var(--bs-link-color); }

.file-path {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.78rem;
}

.file-stats {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.72rem;
}

.chevron-icon {
  transition: transform 0.2s ease;
}

.chevron-icon.expanded {
  transform: rotate(90deg);
}
</style>
