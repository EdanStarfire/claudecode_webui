<template>
  <div class="sandbox-tab">
    <!-- Enable Sandbox Toggle -->
    <div class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-enabled"
        :checked="formData.sandbox_enabled"
        @change="$emit('update:form-data', 'sandbox_enabled', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-enabled">
        Enable Sandbox Mode
      </label>
      <div class="form-text" :class="formData.sandbox_enabled ? 'text-info' : 'text-muted'">
        <small>
          {{ formData.sandbox_enabled
            ? 'Sandbox enabled: Session will have OS-level isolation restricting file system and network access.'
            : 'Sandbox mode restricts file system and network access for added security.'
          }}
        </small>
      </div>
    </div>

    <!-- Core Settings -->
    <h6 class="mt-3 mb-2" :class="{ 'text-muted': !formData.sandbox_enabled }">Core Settings</h6>

    <div class="mb-2 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-auto-bash"
        :checked="formData.sandbox.autoAllowBashIfSandboxed"
        :disabled="!formData.sandbox_enabled"
        @change="updateSandboxField('autoAllowBashIfSandboxed', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-auto-bash" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Auto-allow Bash when sandboxed
      </label>
    </div>

    <div class="mb-2 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-unsandboxed-cmds"
        :checked="formData.sandbox.allowUnsandboxedCommands"
        :disabled="!formData.sandbox_enabled"
        @change="updateSandboxField('allowUnsandboxedCommands', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-unsandboxed-cmds" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Allow unsandboxed commands
      </label>
    </div>

    <div class="mb-2">
      <label for="config-sandbox-excluded-cmds" class="form-label" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Excluded Commands
      </label>
      <input
        type="text"
        class="form-control form-control-sm"
        id="config-sandbox-excluded-cmds"
        :value="formData.sandbox.excludedCommands"
        :disabled="!formData.sandbox_enabled"
        @input="updateSandboxField('excludedCommands', $event.target.value)"
        placeholder="e.g., rm, curl, wget (comma-separated)"
      />
    </div>

    <div class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-weaker-nested"
        :checked="formData.sandbox.enableWeakerNestedSandbox"
        :disabled="!formData.sandbox_enabled"
        @change="updateSandboxField('enableWeakerNestedSandbox', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-weaker-nested" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Enable weaker nested sandbox
      </label>
    </div>

    <!-- Network Settings -->
    <h6 class="mt-3 mb-2" :class="{ 'text-muted': !formData.sandbox_enabled }">Network Settings</h6>

    <div class="mb-2">
      <label for="config-sandbox-allowed-domains" class="form-label" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Allowed Domains
      </label>
      <input
        type="text"
        class="form-control form-control-sm"
        id="config-sandbox-allowed-domains"
        :value="formData.sandbox.network.allowedDomains"
        :disabled="!formData.sandbox_enabled"
        @input="updateNetworkField('allowedDomains', $event.target.value)"
        placeholder="e.g., api.example.com, cdn.example.com (comma-separated)"
      />
    </div>

    <div class="mb-2 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-local-binding"
        :checked="formData.sandbox.network.allowLocalBinding"
        :disabled="!formData.sandbox_enabled"
        @change="updateNetworkField('allowLocalBinding', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-local-binding" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Allow local binding
      </label>
    </div>

    <div class="mb-2">
      <label for="config-sandbox-unix-sockets" class="form-label" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Allow Unix Sockets
      </label>
      <input
        type="text"
        class="form-control form-control-sm"
        id="config-sandbox-unix-sockets"
        :value="formData.sandbox.network.allowUnixSockets"
        :disabled="!formData.sandbox_enabled"
        @input="updateNetworkField('allowUnixSockets', $event.target.value)"
        placeholder="e.g., /var/run/docker.sock (comma-separated)"
      />
    </div>

    <div class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox-all-unix"
        :checked="formData.sandbox.network.allowAllUnixSockets"
        :disabled="!formData.sandbox_enabled"
        @change="updateNetworkField('allowAllUnixSockets', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox-all-unix" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Allow all Unix sockets
      </label>
    </div>

    <!-- Violation Handling -->
    <h6 class="mt-3 mb-2" :class="{ 'text-muted': !formData.sandbox_enabled }">Violation Handling</h6>

    <div class="mb-2">
      <label for="config-sandbox-ignore-file" class="form-label" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Ignore File Violations
      </label>
      <input
        type="text"
        class="form-control form-control-sm"
        id="config-sandbox-ignore-file"
        :value="formData.sandbox.ignoreViolations.file"
        :disabled="!formData.sandbox_enabled"
        @input="updateViolationField('file', $event.target.value)"
        placeholder="File paths to ignore (comma-separated)"
      />
    </div>

    <div class="mb-2">
      <label for="config-sandbox-ignore-network" class="form-label" :class="{ 'text-muted': !formData.sandbox_enabled }">
        Ignore Network Violations
      </label>
      <input
        type="text"
        class="form-control form-control-sm"
        id="config-sandbox-ignore-network"
        :value="formData.sandbox.ignoreViolations.network"
        :disabled="!formData.sandbox_enabled"
        @input="updateViolationField('network', $event.target.value)"
        placeholder="Network patterns to ignore (comma-separated)"
      />
    </div>
  </div>
</template>

<script setup>

const props = defineProps({
  mode: {
    type: String,
    required: true
  },
  formData: {
    type: Object,
    required: true
  },
  errors: {
    type: Object,
    required: true
  },
  session: {
    type: Object,
    default: null
  },
  fieldStates: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['update:form-data'])

function updateSandboxField(field, value) {
  props.formData.sandbox[field] = value
}

function updateNetworkField(field, value) {
  props.formData.sandbox.network[field] = value
}

function updateViolationField(field, value) {
  props.formData.sandbox.ignoreViolations[field] = value
}
</script>

<style scoped>
h6 {
  font-weight: 600;
  border-bottom: 1px solid var(--bs-border-color);
  padding-bottom: 0.25rem;
}
</style>
