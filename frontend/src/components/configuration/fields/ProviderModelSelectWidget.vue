<template>
  <select
    class="form-select form-select-sm"
    :value="value || ''"
    :disabled="disabled || !providerId"
    @change="handleChange"
  >
    <option value="" :disabled="!!providerId && models.length > 0">
      {{ placeholderLabel }}
    </option>
    <option
      v-for="model in models"
      :key="model.id"
      :value="model.id"
    >
      {{ model.display_name }}
    </option>
  </select>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'

const props = defineProps({
  value: { type: String, default: null },
  providerId: { type: String, default: null },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

const store = useProviderCatalogStore()

const models = computed(() => store.modelsForEntry(props.providerId))

const placeholderLabel = computed(() => {
  if (!props.providerId) return 'Select a Provider first'
  if (models.value.length === 0) return 'No models exposed by this provider'
  return 'Select model...'
})

function handleChange(event) {
  emit('update:value', event.target.value || null)
}

onMounted(() => {
  store.fetchIfEmpty()
})
</script>
