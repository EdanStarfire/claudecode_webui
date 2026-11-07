<template>
  <div class="relay-prompt" data-testid="relay-permission-prompt">
    <div class="detail-banner detail-banner-warning">
      <strong>{{ title }}</strong>
      <p>
        <span class="relay-action">{{ actionLabel }}</span>
        <code class="relay-channel" data-testid="relay-channel">{{ channelLabel }}</code>
      </p>
    </div>

    <pre v-if="prompt.text_preview" class="relay-preview" data-testid="relay-preview">{{ prompt.text_preview }}</pre>

    <!-- Unlisted channel: choose a policy -->
    <fieldset v-if="isChoosePolicy" class="relay-policies" :disabled="disabled">
      <legend class="relay-legend">Policy for this channel (saved for this session)</legend>
      <label v-for="opt in POLICY_OPTIONS" :key="opt.value" class="relay-policy-option">
        <input
          type="radio"
          name="relay-policy"
          :value="opt.value"
          v-model="selectedPolicy"
          :data-testid="`relay-policy-${opt.value}`"
        />
        <span class="relay-policy-name">{{ opt.label }}</span>
        <span class="relay-policy-desc">{{ opt.description }}</span>
      </label>
    </fieldset>

    <div class="permission-buttons">
      <template v-if="isChoosePolicy">
        <button
          class="btn-timeline btn-primary"
          :disabled="disabled || !selectedPolicy"
          data-testid="relay-save-policy"
          @click="$emit('decide', { decision: 'allow', relayChoice: { policy: selectedPolicy, promote: false } })"
        >{{ saveLabel }}</button>
      </template>
      <template v-else>
        <button
          class="btn-timeline btn-approve-outline"
          :disabled="disabled"
          data-testid="relay-allow-once"
          @click="$emit('decide', { decision: 'allow', relayChoice: null })"
        >Allow once</button>
        <button
          class="btn-timeline btn-approve"
          :disabled="disabled"
          data-testid="relay-allow-auto"
          @click="$emit('decide', { decision: 'allow', relayChoice: { policy: null, promote: true } })"
        >Allow and set channel to auto</button>
      </template>
      <button
        class="btn-timeline btn-deny"
        :disabled="disabled"
        data-testid="relay-deny"
        @click="$emit('decide', { decision: 'deny', relayChoice: null })"
      >Deny</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

// Issue #2103 (outbound-1b): relay permission prompt. Two kinds from the backend permission
// pre-hook: `ask_send` (send on an `ask` channel) and `choose_policy` (first touch of an
// unlisted channel). Emits `decide` with the REST `relay_choice` shape (camelCase here,
// forwarded by the polling store).
const props = defineProps({
  prompt: { type: Object, required: true },
  disabled: { type: Boolean, default: false }
})
defineEmits(['decide'])

const POLICY_OPTIONS = [
  { value: 'blocked', label: 'Blocked', description: 'No reading or sending; hidden from search' },
  { value: 'read_only', label: 'Read only', description: 'Read freely; sending is refused' },
  { value: 'ask', label: 'Ask', description: 'Read freely; each send asks you first' },
  { value: 'auto', label: 'Auto', description: 'Read and send freely' }
]

const ACTION_LABELS = {
  post: 'Post a message to',
  reply: 'Reply in a thread in',
  upload_file: 'Upload a file to',
  react: 'Add a reaction in',
  read_thread: 'Read a thread in',
  mark_read: 'Mark as read in'
}

const isChoosePolicy = computed(() => props.prompt.kind === 'choose_policy')
const title = computed(() =>
  isChoosePolicy.value ? 'Choose a policy for a Slack channel' : 'Slack send needs approval'
)
const actionLabel = computed(() => ACTION_LABELS[props.prompt.tool] || 'Use')
// Plain text only (Vue escapes it): the backend resolves "#name", "DM with <name>" or
// "your self-DM", falling back to the raw ID when a lookup failed.
const channelLabel = computed(() => props.prompt.channel_label || props.prompt.channel_id)
const selectedPolicy = ref('ask')
const saveLabel = computed(() =>
  selectedPolicy.value === 'blocked' ? 'Save and block' : 'Save policy and continue'
)
</script>

<style scoped>
.relay-prompt { width: 100%; font-size: 13px; }

/* Same look as PermissionPrompt.vue (its styles are scoped to that component). */
.detail-banner {
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  font-size: 12px;
}
.detail-banner strong { display: block; margin-bottom: 2px; }
.detail-banner p { margin: 2px 0 0; opacity: 0.8; }
.detail-banner-warning {
  background: #fef3c7;
  border: 1px solid #fbbf24;
  color: #92400e;
}
.permission-buttons {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.btn-timeline {
  padding: 4px 12px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  background: white;
  color: #334155;
}
.btn-timeline:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-approve { background: #22c55e; border-color: #22c55e; color: white; }
.btn-approve:hover:not(:disabled) { background: #16a34a; }
.btn-approve-outline { border-color: #22c55e; color: #16a34a; }
.btn-approve-outline:hover:not(:disabled) { background: #f0fdf4; }
.btn-deny { background: #ef4444; border-color: #ef4444; color: white; }
.btn-deny:hover:not(:disabled) { background: #dc2626; }
.btn-primary { background: #3b82f6; border-color: #3b82f6; color: white; }
.btn-primary:hover:not(:disabled) { background: #2563eb; }
.relay-action { margin-right: 4px; }
.relay-channel { font-size: 12px; }
.relay-preview {
  margin: 6px 0;
  padding: 6px 8px;
  max-height: 10em;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  color: var(--bs-body-color);
}
.relay-policies { border: 0; padding: 0; margin: 6px 0; }
.relay-legend { font-size: 11px; font-weight: 600; margin-bottom: 4px; color: var(--bs-secondary-color); }
.relay-policy-option {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
  padding: 3px 0;
  font-size: 12px;
  cursor: pointer;
}
.relay-policy-name { font-weight: 600; }
.relay-policy-desc { color: var(--bs-secondary-color); font-size: 11px; }
</style>
