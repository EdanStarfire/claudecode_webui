<template>
  <div class="sandbox-sub-section">
    <!-- Bash Permissions -->
    <div class="sandbox-section-label">Bash Permissions</div>
    <div class="form-check mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('auto-bash')"
        :checked="val.autoAllowBashIfSandboxed"
        :disabled="disabled"
        @change="update('autoAllowBashIfSandboxed', $event.target.checked)" />
      <label class="form-check-label" :for="id('auto-bash')" style="text-transform: none; letter-spacing: normal;">
        Auto-allow Bash when sandboxed
      </label>
    </div>
    <div class="form-check mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('unsandboxed')"
        :checked="val.allowUnsandboxedCommands"
        :disabled="disabled"
        @change="update('allowUnsandboxedCommands', $event.target.checked)" />
      <label class="form-check-label" :for="id('unsandboxed')" style="text-transform: none; letter-spacing: normal;">
        Allow unsandboxed commands
      </label>
    </div>
    <div class="mb-2 ms-3">
      <label class="form-label">Excluded Commands</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.excludedCommands"
        :disabled="disabled"
        @input="update('excludedCommands', $event.target.value)"
        placeholder="rm, dd, mkfs..." />
    </div>
    <div class="form-check mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('weaker')"
        :checked="val.enableWeakerNestedSandbox"
        :disabled="disabled"
        @change="update('enableWeakerNestedSandbox', $event.target.checked)" />
      <label class="form-check-label" :for="id('weaker')" style="text-transform: none; letter-spacing: normal;">
        Enable weaker nested sandbox
      </label>
    </div>

    <!-- Network -->
    <div class="sandbox-section-label">Network</div>
    <div class="mb-1 ms-3">
      <label class="form-label">Allowed Domains</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.network?.allowedDomains"
        :disabled="disabled"
        @input="updateNetwork('allowedDomains', $event.target.value)"
        placeholder="github.com, api.example.com" />
    </div>
    <div class="form-check mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('local-binding')"
        :checked="val.network?.allowLocalBinding"
        :disabled="disabled"
        @change="updateNetwork('allowLocalBinding', $event.target.checked)" />
      <label class="form-check-label" :for="id('local-binding')" style="text-transform: none; letter-spacing: normal;">
        Allow local binding
      </label>
    </div>
    <div class="mb-1 ms-3">
      <label class="form-label">Allow Unix Sockets</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.network?.allowUnixSockets"
        :disabled="disabled"
        @input="updateNetwork('allowUnixSockets', $event.target.value)"
        placeholder="/var/run/docker.sock" />
    </div>
    <div class="form-check mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('all-unix')"
        :checked="val.network?.allowAllUnixSockets"
        :disabled="disabled"
        @change="updateNetwork('allowAllUnixSockets', $event.target.checked)" />
      <label class="form-check-label" :for="id('all-unix')" style="text-transform: none; letter-spacing: normal;">
        Allow all Unix sockets
      </label>
    </div>

    <!-- Violation Handling -->
    <div class="sandbox-section-label">Violation Handling</div>
    <div class="mb-1 ms-3">
      <label class="form-label">Ignore File Violations</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.ignoreViolations?.file"
        :disabled="disabled"
        @input="updateViolation('file', $event.target.value)"
        placeholder="File paths to ignore" />
    </div>
    <div class="mb-1 ms-3">
      <label class="form-label">Ignore Network Violations</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.ignoreViolations?.network"
        :disabled="disabled"
        @input="updateViolation('network', $event.target.value)"
        placeholder="Network patterns to ignore" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

const prefix = Math.random().toString(36).slice(2, 7)
function id(suffix) { return `sb-${prefix}-${suffix}` }

const val = computed(() => props.value || {})

function update(field, newVal) {
  emit('update:value', { ...val.value, [field]: newVal })
}

function updateNetwork(field, newVal) {
  emit('update:value', {
    ...val.value,
    network: { ...(val.value.network || {}), [field]: newVal },
  })
}

function updateViolation(field, newVal) {
  emit('update:value', {
    ...val.value,
    ignoreViolations: { ...(val.value.ignoreViolations || {}), [field]: newVal },
  })
}
</script>

<style scoped>
.sandbox-sub-section .form-label {
  font-size: 0.8rem;
  margin-bottom: 2px;
}
</style>
