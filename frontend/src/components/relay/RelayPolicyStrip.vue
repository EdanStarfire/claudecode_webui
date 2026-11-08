<template>
  <div class="policy-strip" role="radiogroup" :aria-label="label" :class="{ disabled }" data-testid="relay-policy-strip">
    <button
      v-for="v in model.values"
      :key="v"
      type="button"
      role="radio"
      class="strip-btn"
      :class="{ selected: v === modelValue }"
      :aria-checked="String(v === modelValue)"
      :aria-label="v"
      :title="model.value_help?.[v] ? `${v}: ${model.value_help[v]}` : v"
      :disabled="disabled"
      :data-testid="`relay-policy-opt-${v}`"
      @click="v !== modelValue && $emit('update:modelValue', v)"
    >
      <span class="strip-icon" aria-hidden="true">{{ model.value_icons?.[v] || v.charAt(0).toUpperCase() }}</span>
      <span class="strip-word">{{ v }}</span>
    </button>
  </div>
</template>

<script setup>
/** Single-selection toggle strip over the policy model's values (icon + word; icon only on phones). */
defineProps({
  model: { type: Object, required: true },
  modelValue: { type: String, default: '' },
  label: { type: String, default: 'Policy' },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<style scoped>
.policy-strip { display: inline-flex; flex: none; border: 1px solid var(--bs-border-color); border-radius: 6px; overflow: hidden; }
.strip-btn {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; font-size: 12px; line-height: 1.5;
  background: var(--bs-body-bg); color: var(--bs-secondary-color); border: 0; border-left: 1px solid var(--bs-border-color); cursor: pointer;
  white-space: nowrap;
}
.strip-btn:first-child { border-left: 0; }
.strip-btn:hover:not(:disabled):not(.selected) { background: var(--bs-secondary-bg); }
.strip-btn.selected { background: rgba(99, 102, 241, 0.18); color: var(--bs-emphasis-color); }
.policy-strip.disabled { opacity: 0.6; }
@media (max-width: 600px) {
  .strip-btn { padding: 2px 6px; }
  .strip-word { display: none; }
}
</style>
