<template>
  <div class="default-footer" data-testid="relay-default-footer">
    <div class="df-head">Default footer</div>
    <p class="df-hint">
      Leave empty for the built-in text. Applies to every session on this relay unless its profile,
      template or session sets its own. A session can only use the built-in text instead by writing
      it out itself. Applies immediately.
    </p>
    <div class="df-row">
      <label :for="`${uid}-mode`" class="df-label">Footer</label>
      <select :id="`${uid}-mode`" v-model="mode" class="form-select form-select-sm df-mode" data-testid="relay-footer-mode">
        <option value="inherit">Built-in (on)</option>
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
    </div>
    <div v-if="mode !== 'off'" class="df-row df-row--text">
      <label :for="`${uid}-text`" class="df-label">Text</label>
      <div class="df-text">
        <textarea
          :id="`${uid}-text`"
          v-model="text"
          class="form-control form-control-sm"
          rows="2"
          :placeholder="BUILT_IN_FOOTER_TEMPLATE"
          data-testid="relay-footer-text"
        ></textarea>
        <div class="df-meta">
          <small class="df-vars">Variables: {session_name}, {session_handle}, {session_link}. No !word.</small>
          <small :class="{ 'df-over': tooLong }" data-testid="relay-footer-counter">{{ text.length }} / {{ FOOTER_MAX_LENGTH }}</small>
        </div>
        <small v-if="tooLong" class="df-error" role="alert" data-testid="relay-footer-error">
          At most {{ FOOTER_MAX_LENGTH }} characters.
        </small>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { BUILT_IN_FOOTER_TEMPLATE, FOOTER_MAX_LENGTH } from '@/utils/relayFooter'

const props = defineProps({
  // Public relay record (default_footer_enabled / default_footer_template).
  relay: { type: Object, required: true },
})
const emit = defineEmits(['change'])

const uid = `relay-footer-${Math.random().toString(36).slice(2, 8)}`
const modeOf = (enabled) => (enabled === true ? 'on' : enabled === false ? 'off' : 'inherit')
const savedEnabled = (mode) => (mode === 'on' ? true : mode === 'off' ? false : null)

const mode = ref(modeOf(props.relay.default_footer_enabled))
const text = ref(props.relay.default_footer_template || '')
watch(() => [props.relay.default_footer_enabled, props.relay.default_footer_template], ([enabled, template]) => {
  mode.value = modeOf(enabled)
  text.value = template || ''
})

const tooLong = computed(() => text.value.length > FOOTER_MAX_LENGTH)
const normalizedText = computed(() => (text.value.trim() ? text.value : null))

// Only changed fields; empty text is sent as null (clears the relay text).
const patch = computed(() => {
  const out = {}
  if (savedEnabled(mode.value) !== (props.relay.default_footer_enabled ?? null)) {
    out.default_footer_enabled = savedEnabled(mode.value)
  }
  if (normalizedText.value !== (props.relay.default_footer_template || null)) {
    out.default_footer_template = normalizedText.value
  }
  return out
})

watch([patch, tooLong], () => {
  emit('change', { dirty: Object.keys(patch.value).length > 0, valid: !tooLong.value, patch: patch.value })
}, { immediate: true })
</script>

<style scoped>
.default-footer { display: flex; flex-direction: column; gap: 8px; }
.df-head { font-size: 13px; font-weight: 600; color: var(--bs-emphasis-color); }
.df-hint { margin: 0; font-size: 12px; line-height: 1.5; color: var(--bs-secondary-color); }
.df-row { display: flex; align-items: center; gap: 12px; }
.df-row--text { align-items: flex-start; }
.df-label { font-size: 12px; font-weight: 600; color: var(--bs-emphasis-color); width: 56px; flex: 0 0 56px; margin: 0; }
.df-mode { max-width: 200px; }
.df-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.df-meta { display: flex; justify-content: space-between; gap: 8px; color: var(--bs-secondary-color); }
.df-over, .df-error { color: var(--tool-error-text); }
@media (max-width: 600px) {
  .df-row { flex-direction: column; align-items: stretch; gap: 4px; }
  .df-label { width: auto; flex: none; }
  .df-vars { display: none; }
}
</style>
