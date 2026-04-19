<template>
  <div>
    <!-- Selected items as dismissible badges -->
    <div v-if="selectedList.length" class="d-flex gap-1 mb-1 flex-wrap">
      <span
        v-for="item in selectedList"
        :key="item"
        class="badge bg-info-subtle text-info-emphasis d-flex align-items-center gap-1"
      >
        {{ item }}
        <button
          type="button"
          class="btn-close btn-close-sm"
          :disabled="disabled"
          @click="removeItem(item)"
          aria-label="Remove"
          style="font-size: 0.6rem;"
        ></button>
      </span>
    </div>

    <!-- Dropdown to add more items from available options -->
    <div class="input-group input-group-sm">
      <select
        class="form-select form-select-sm"
        :disabled="disabled || availableToAdd.length === 0"
        v-model="pendingAdd"
        @change="addSelected"
      >
        <option value="">{{ availableToAdd.length ? placeholder : 'No credentials available' }}</option>
        <option v-for="opt in availableToAdd" :key="opt" :value="opt">{{ opt }}</option>
      </select>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useProxyStore } from '@/stores/proxy'

const props = defineProps({
  value: { type: [String, Array], default: '' },
  disabled: { type: Boolean, default: false },
  optionsFrom: { type: String, default: null },
  placeholder: { type: String, default: 'Add...' },
})

const emit = defineEmits(['update:value'])

const pendingAdd = ref('')

// Resolve options source
const proxyStore = useProxyStore()

const allOptions = computed(() => {
  if (props.optionsFrom === 'proxyCredentials') {
    return proxyStore.credentials.map(c => c.name)
  }
  return []
})

// Support both comma-separated string and array input
const selectedList = computed(() => {
  if (!props.value) return []
  if (Array.isArray(props.value)) return props.value.filter(Boolean)
  return props.value.split(',').map(s => s.trim()).filter(Boolean)
})

// Options not yet selected
const availableToAdd = computed(() => {
  return allOptions.value.filter(opt => !selectedList.value.includes(opt))
})

function addSelected() {
  if (!pendingAdd.value) return
  emit('update:value', [...selectedList.value, pendingAdd.value])
  pendingAdd.value = ''
}

function removeItem(item) {
  emit('update:value', selectedList.value.filter(s => s !== item))
}
</script>
