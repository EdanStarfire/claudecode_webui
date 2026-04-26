<template>
  <span v-if="usage" class="position-relative d-inline-block" ref="badgeRef">
    <button
      type="button"
      class="btn btn-sm badge bg-light text-dark border border-secondary-subtle px-2 py-1"
      style="font-size: 0.75rem; font-weight: 500; cursor: pointer;"
      :title="popoverOpen ? '' : 'Click for cost breakdown'"
      @click.stop="togglePopover"
    >
      {{ badgeLabel }}
    </button>

    <div
      v-if="popoverOpen"
      class="cost-popover card shadow"
      role="dialog"
      aria-label="Token usage breakdown"
    >
      <div class="card-header d-flex justify-content-between align-items-center py-2 px-3">
        <span class="fw-semibold small">Token Usage</span>
        <button
          type="button"
          class="btn-close btn-close-sm"
          aria-label="Close"
          @click.stop="popoverOpen = false"
        ></button>
      </div>
      <div class="card-body py-2 px-3">
        <!-- Model info -->
        <div class="d-flex justify-content-between small text-muted mb-2">
          <span>Model</span>
          <span class="font-monospace">{{ usage.model || 'unknown' }}</span>
        </div>
        <div class="d-flex justify-content-between small text-muted mb-2">
          <span>Turns</span>
          <span>{{ usage.turn_count }}</span>
        </div>

        <hr class="my-2" />

        <!-- Token breakdown table -->
        <table class="table table-borderless table-sm small mb-0">
          <thead>
            <tr class="text-muted">
              <th class="ps-0">Category</th>
              <th class="text-end">Tokens</th>
              <th class="text-end pe-0">Cost</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="ps-0">Input</td>
              <td class="text-end">{{ formatTokens(usage.input_tokens) }}</td>
              <td class="text-end pe-0">{{ formatCost(rowCost('input')) }}</td>
            </tr>
            <tr>
              <td class="ps-0">Output</td>
              <td class="text-end">{{ formatTokens(usage.output_tokens) }}</td>
              <td class="text-end pe-0">{{ formatCost(rowCost('output')) }}</td>
            </tr>
            <tr>
              <td class="ps-0">Cache write</td>
              <td class="text-end">{{ formatTokens(usage.cache_write_tokens) }}</td>
              <td class="text-end pe-0">{{ formatCost(rowCost('cache_write')) }}</td>
            </tr>
            <tr>
              <td class="ps-0">Cache read</td>
              <td class="text-end">{{ formatTokens(usage.cache_read_tokens) }}</td>
              <td class="text-end pe-0">{{ formatCost(rowCost('cache_read')) }}</td>
            </tr>
          </tbody>
        </table>

        <hr class="my-2" />

        <!-- Totals -->
        <div class="d-flex justify-content-between small fw-semibold mb-1">
          <span>Total tokens</span>
          <span>{{ formatTokens(totalTokens) }}</span>
        </div>
        <div class="d-flex justify-content-between small fw-semibold mb-1">
          <span>Estimated cost</span>
          <span>{{ estimatedCostLabel }}</span>
        </div>
        <div
          v-if="usage.sdk_reported_cost_usd != null"
          class="d-flex justify-content-between small text-muted"
        >
          <span>SDK reported cost</span>
          <span>${{ usage.sdk_reported_cost_usd.toFixed(4) }}</span>
        </div>

        <!-- Unknown model note -->
        <div
          v-if="!usage.rates_known"
          class="alert alert-warning py-1 px-2 mt-2 mb-0 small"
          role="alert"
        >
          Unknown model — pricing rates not available. Update
          <code>~/.config/cc_webui/config.json</code> to add rates.
        </div>

        <!-- Rates used -->
        <div v-if="usage.rates_used" class="mt-2">
          <div class="text-muted small mb-1">Rates (USD / 1M tokens)</div>
          <div class="d-flex flex-wrap gap-2 small font-monospace text-muted">
            <span>in:${{ usage.rates_used.input }}</span>
            <span>out:${{ usage.rates_used.output }}</span>
            <span>cw:${{ usage.rates_used.cache_write }}</span>
            <span>cr:${{ usage.rates_used.cache_read }}</span>
          </div>
        </div>
      </div>
    </div>
  </span>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useUsageStore } from '@/stores/usage'

const props = defineProps({
  sessionId: {
    type: String,
    required: true,
  },
})

const usageStore = useUsageStore()
const badgeRef = ref(null)
const popoverOpen = ref(false)

const usage = computed(() => usageStore.usageBySession.get(props.sessionId) || null)

const totalTokens = computed(() => {
  if (!usage.value) return 0
  return (
    (usage.value.input_tokens || 0) +
    (usage.value.output_tokens || 0) +
    (usage.value.cache_write_tokens || 0) +
    (usage.value.cache_read_tokens || 0)
  )
})

const badgeLabel = computed(() => {
  if (!usage.value) return ''
  const cost = usage.value.estimated_cost_usd
  const costStr = cost != null
    ? (usage.value.rates_known ? `~$${cost.toFixed(3)}` : '~$?')
    : '~$?'
  return `${costStr} · ${formatTokens(totalTokens.value)}`
})

const estimatedCostLabel = computed(() => {
  if (!usage.value) return '—'
  const cost = usage.value.estimated_cost_usd
  if (cost == null || !usage.value.rates_known) return '~$? (unknown model)'
  return `~$${cost.toFixed(4)}`
})

function rowCost(category) {
  if (!usage.value?.rates_used) return null
  const tokens = usage.value[`${category}_tokens`] || 0
  const rate = usage.value.rates_used[category] || 0
  return (tokens / 1_000_000) * rate
}

function formatTokens(n) {
  if (n == null) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatCost(cost) {
  if (cost == null) return '—'
  return `$${cost.toFixed(4)}`
}

function togglePopover() {
  popoverOpen.value = !popoverOpen.value
}

function handleClickOutside(event) {
  if (popoverOpen.value && badgeRef.value && !badgeRef.value.contains(event.target)) {
    popoverOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.cost-popover {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 1060;
  min-width: 280px;
  max-width: 340px;
}
</style>
