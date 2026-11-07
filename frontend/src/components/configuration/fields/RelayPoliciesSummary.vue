<template>
  <div class="policies-summary" data-testid="relay-policies-summary">
    <strong>{{ count }}</strong> channel {{ count === 1 ? 'policy' : 'policies' }} on this tier.
    <span class="note">Managed in the policy editor; not changed from this form.</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ value: { type: Object, default: null } })
const count = computed(() => Object.keys(props.value || {}).length)
</script>

<style scoped>
.policies-summary { font-size: 13px; padding-top: 4px; }
.note { color: var(--bs-secondary-color); font-size: 12px; margin-left: 4px; }
</style>
