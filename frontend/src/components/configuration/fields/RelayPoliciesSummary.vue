<template>
  <div class="policies-summary" data-testid="relay-policies-summary">
    <strong>{{ count }}</strong> channel {{ count === 1 ? 'policy' : 'policies' }} on this tier.
    <span class="note">Policy editor arrives in the next update.</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ value: { type: Object, default: null } })
// Stored shape is { relay_id: { target: policy } }; a legacy flat entry (string) counts as one.
const count = computed(() => Object.values(props.value || {}).reduce(
  (n, v) => n + (v && typeof v === 'object' ? Object.keys(v).length : 1), 0))
</script>

<style scoped>
.policies-summary { font-size: 13px; padding-top: 4px; }
.note { color: var(--bs-secondary-color); font-size: 12px; margin-left: 4px; }
</style>
