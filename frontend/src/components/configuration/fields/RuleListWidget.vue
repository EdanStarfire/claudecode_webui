<template>
  <div>
    <div class="rule-rows mb-1">
      <div
        v-for="(rule, index) in ruleList"
        :key="index"
        class="rule-row d-flex align-items-center gap-2 mb-1"
      >
        <input
          type="text"
          class="form-control form-control-sm rule-text"
          :class="{ 'rule-text--defaults': rule === '$defaults' }"
          :value="rule"
          :disabled="disabled"
          @change="updateRule(index, $event.target.value)"
        />
        <button
          type="button"
          class="btn-close btn-close-sm"
          :disabled="disabled"
          @click="removeRule(index)"
          aria-label="Remove rule"
        ></button>
      </div>
    </div>
    <div class="input-group input-group-sm">
      <input
        type="text"
        class="form-control form-control-sm"
        :placeholder="placeholder"
        v-model="newRule"
        :disabled="disabled"
        @keydown.enter.prevent="addRule"
      />
      <button
        type="button"
        class="btn btn-outline-secondary"
        :disabled="disabled || !newRule.trim()"
        @click="addRule"
      >Add</button>
    </div>
    <div v-if="showWarning" class="rule-warning mt-2">
      <span class="rule-warning-icon">&#9888;</span>
      <span class="rule-warning-text">
        No <code>"$defaults"</code> row present, so this fully replaces the built-in rules for
        this field &mdash; only the rule(s) listed above will apply. Add a row containing
        literally <code>"$defaults"</code> to keep the built-ins too.
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  value: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: 'Add...' },
  warnIfNoDefaults: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

const newRule = ref('')

const ruleList = computed(() => Array.isArray(props.value) ? props.value : [])

const showWarning = computed(() =>
  props.warnIfNoDefaults && ruleList.value.length > 0 && !ruleList.value.includes('$defaults')
)

function addRule() {
  const rule = newRule.value.trim()
  if (!rule) return
  emit('update:value', [...ruleList.value, rule])
  newRule.value = ''
}

function removeRule(index) {
  const list = [...ruleList.value]
  list.splice(index, 1)
  emit('update:value', list)
}

function updateRule(index, text) {
  const trimmed = text.trim()
  const list = [...ruleList.value]
  if (!trimmed) {
    list.splice(index, 1)
  } else {
    list[index] = trimmed
  }
  emit('update:value', list)
}
</script>

<style scoped>
.rule-text--defaults {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #3730a3;
  background-color: #eef2ff;
  border-color: #c7d2fe;
}

.rule-warning {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: #fff8e6;
  border: 1px solid #f0c36d;
  color: #7a5b00;
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 12px;
}

.rule-warning-icon {
  flex-shrink: 0;
}

.rule-warning-text {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
