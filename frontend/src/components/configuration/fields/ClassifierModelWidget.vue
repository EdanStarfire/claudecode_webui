<template>
  <div>
    <input
      type="text"
      class="form-control form-control-sm font-monospace"
      :value="value || ''"
      :disabled="disabled"
      :placeholder="placeholder"
      :list="listId"
      @input="$emit('update:value', $event.target.value || null)"
    />
    <datalist :id="listId">
      <option
        v-for="opt in catalogOptions"
        :key="opt.alias"
        :value="opt.alias"
      >
        {{ opt.label }}
      </option>
    </datalist>
    <small v-if="isCatalogAlias" class="text-muted d-block mt-1">
      LiteLLM catalog model — only used on sessions routed through LiteLLM; ignored otherwise.
    </small>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'

const props = defineProps({
  value: { type: String, default: null },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
})

defineEmits(['update:value'])

// Must match MODEL_ALIAS_SEP in backend/litellm_proxy_manager.py
const MODEL_ALIAS_SEP = '--'

const store = useProviderCatalogStore()
const listId = `classifier-model-list-${Math.random().toString(36).slice(2, 8)}`

const catalogOptions = computed(() =>
  store.entries.flatMap(entry =>
    (entry.models || []).map(model => ({
      alias: `${entry.id}${MODEL_ALIAS_SEP}${model.id}`,
      label: `${entry.name || entry.id}: ${model.litellm_model || model.id}`,
    }))
  )
)

const isCatalogAlias = computed(() =>
  catalogOptions.value.some(opt => opt.alias === props.value)
)

onMounted(() => {
  store.fetchIfEmpty()
})
</script>
