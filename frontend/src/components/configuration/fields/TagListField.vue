<template>
  <div>
    <div class="d-flex gap-1 mb-1 flex-wrap">
      <span
        v-for="(domain, index) in domainList"
        :key="index"
        class="badge bg-success-subtle text-success-emphasis d-flex align-items-center gap-1"
      >
        {{ domain }}
        <button
          type="button"
          class="btn-close btn-close-sm"
          :disabled="disabled"
          @click="removeDomain(index)"
          aria-label="Remove"
          style="font-size: 0.6rem;"
        ></button>
      </span>
    </div>
    <div class="input-group input-group-sm">
      <input
        ref="inputEl"
        type="text"
        class="form-control form-control-sm"
        :placeholder="placeholder"
        v-model="newDomain"
        :disabled="disabled"
        @keydown.enter.prevent="addDomain"
      />
      <button
        type="button"
        class="btn btn-outline-secondary"
        :disabled="disabled || !newDomain.trim()"
        @click="addDomain"
      >Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  value: { type: [String, Array], default: '' },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: 'e.g., api.example.com' },
})

const emit = defineEmits(['update:value'])

const inputEl = ref(null)
const newDomain = ref('')

// Support both comma-separated string and array input
const domainList = computed(() => {
  if (!props.value) return []
  if (Array.isArray(props.value)) return props.value.filter(Boolean)
  return props.value.split(',').map(d => d.trim()).filter(Boolean)
})

function addDomain() {
  const domain = newDomain.value.trim()
  if (!domain || domainList.value.includes(domain)) {
    newDomain.value = ''
    return
  }
  emit('update:value', [...domainList.value, domain])
  newDomain.value = ''
}

function removeDomain(index) {
  const list = [...domainList.value]
  list.splice(index, 1)
  emit('update:value', list)
}
</script>
