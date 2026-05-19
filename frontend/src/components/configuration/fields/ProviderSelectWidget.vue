<template>
  <div>
    <select
      class="form-select form-select-sm"
      :value="selectValue"
      :disabled="disabled"
      @change="handleChange"
    >
      <option value="">— Use default (Anthropic) —</option>
      <option v-for="entry in store.entries" :key="entry.id" :value="entry.id">
        {{ entry.id }}
      </option>
      <!-- Deleted entry: show disabled option for orphaned id -->
      <option
        v-if="value && !store.getEntry(value) && store.loaded"
        :value="value"
        disabled
      >
        (deleted) {{ value }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'

const props = defineProps({
  value: { type: String, default: null },
  disabled: { type: Boolean, default: false },
  linkedKey: { type: String, default: 'provider_model_id' },
})

const emit = defineEmits(['update:value', 'update:linked'])

const store = useProviderCatalogStore()

const selectValue = computed(() => props.value || '')

function handleChange(event) {
  const newId = event.target.value || null
  emit('update:value', newId)
  if (newId !== props.value) {
    emit('update:linked', { key: props.linkedKey, value: null })
  }
}

onMounted(() => {
  store.fetchIfEmpty()
})
</script>
