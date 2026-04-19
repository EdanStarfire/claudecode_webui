<template>
  <div>
    <div v-for="(dir, index) in dirList" :key="index" class="dir-list-item mb-1">
      <span class="dir-path">{{ dir }}</span>
      <span class="dir-remove" @click="removeDir(index)">&times;</span>
    </div>
    <div v-if="error" class="text-danger small mb-1">{{ error }}</div>
    <div class="d-flex gap-2 mt-1">
      <input
        type="text"
        :class="['form-control', 'form-control-sm', { 'is-invalid': error }]"
        v-model="newDir"
        :disabled="disabled"
        :placeholder="placeholder"
        @keydown.enter.prevent="addDir"
        style="flex: 1;"
      />
      <button
        v-if="showBrowse"
        type="button"
        class="btn btn-sm btn-outline-secondary"
        :disabled="disabled"
        @click="$emit('browse')"
        title="Browse"
      >&#x1F4C2;</button>
      <button
        type="button"
        class="btn btn-sm btn-outline-primary"
        :disabled="disabled || !newDir.trim()"
        @click="addDir"
      >+</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { validateTemplatePath } from '@/utils/templateVariables'

const props = defineProps({
  value: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: 'Add directory path...' },
  showBrowse: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value', 'browse'])

const newDir = ref('')
const error = ref(null)

const dirList = computed(() => {
  if (!props.value) return []
  return props.value.split('\n').map(d => d.trim()).filter(Boolean)
})

function addDir() {
  const dir = newDir.value.trim()
  if (!dir) return
  const err = validateTemplatePath(dir)
  if (err) { error.value = err; return }
  error.value = null
  const current = [...dirList.value]
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:value', current.join('\n'))
  }
  newDir.value = ''
}

function removeDir(index) {
  const current = [...dirList.value]
  current.splice(index, 1)
  emit('update:value', current.join('\n'))
}

function addDirectoryPath(dir) {
  const current = [...dirList.value]
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:value', current.join('\n'))
  }
}

defineExpose({ addDirectoryPath })
</script>
