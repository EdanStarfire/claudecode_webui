<template>
  <div>
    <div class="tag-editor" @click="inputEl && inputEl.focus()">
      <span
        v-for="(tag, index) in tagList"
        :key="index"
        class="tag"
        :class="tagClass"
      >
        {{ tag }}
        <span class="tag-remove" @click.stop="removeTag(index)">&times;</span>
      </span>
      <input
        ref="inputEl"
        type="text"
        class="tag-input"
        :placeholder="placeholder"
        v-model="newItem"
        :disabled="disabled"
        @keydown.enter.prevent="addTag"
      />
    </div>
    <div v-if="quickAddItems && quickAddItems.length > 0" class="quick-add-btns mt-1">
      <button
        v-for="item in quickAddItems"
        :key="item"
        type="button"
        class="btn btn-sm"
        :disabled="disabled"
        :class="tagList.includes(item) ? activeClass : inactiveClass"
        @click="toggleTag(item)"
      >{{ tagList.includes(item) ? item : '+' + item }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  value: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  quickAddItems: { type: Array, default: null },
  variant: { type: String, default: 'allowed' }, // 'allowed' | 'disallowed' | 'capability'
  placeholder: { type: String, default: 'Add...' },
})

const emit = defineEmits(['update:value'])

const inputEl = ref(null)
const newItem = ref('')

const tagList = computed(() => {
  if (!props.value || !props.value.trim()) return []
  return props.value.split(',').map(t => t.trim()).filter(Boolean)
})

const tagClass = computed(() => {
  if (props.variant === 'disallowed') return 'tag-disallowed'
  if (props.variant === 'capability') return 'tag-capability'
  return 'tag-allowed'
})

const activeClass = computed(() => {
  if (props.variant === 'disallowed') return 'btn-danger'
  return 'btn-success'
})

const inactiveClass = computed(() => {
  if (props.variant === 'disallowed') return 'btn-outline-danger'
  return 'btn-outline-success'
})

function addTag() {
  const tag = props.variant === 'capability'
    ? newItem.value.trim().toLowerCase()
    : newItem.value.trim()
  if (!tag || tagList.value.includes(tag)) { newItem.value = ''; return }
  emit('update:value', [...tagList.value, tag].join(', '))
  newItem.value = ''
}

function removeTag(index) {
  const list = [...tagList.value]
  list.splice(index, 1)
  emit('update:value', list.join(', '))
}

function toggleTag(item) {
  if (props.disabled) return
  if (tagList.value.includes(item)) {
    removeTag(tagList.value.indexOf(item))
  } else {
    emit('update:value', [...tagList.value, item].join(', '))
  }
}
</script>
