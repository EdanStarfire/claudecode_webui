<template>
  <select
    class="form-select form-select-sm"
    :value="value || ''"
    :disabled="disabled"
    data-testid="relay-select"
    @change="$emit('update:value', $event.target.value || null)"
  >
    <option value="">None</option>
    <option v-for="r in store.relays" :key="r.relay_id" :value="r.relay_id">{{ r.name }}</option>
    <option v-if="value && !known" :value="value">{{ value }} (unavailable)</option>
  </select>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRelayStore } from '@/stores/relay'

const props = defineProps({
  value: { type: String, default: null },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:value'])

const store = useRelayStore()
const known = computed(() => store.relays.some(r => r.relay_id === props.value))
onMounted(() => store.fetchIfEmpty())
</script>
