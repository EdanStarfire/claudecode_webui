<template>
  <div class="row g-3 mb-3">
    <div class="col-6 col-md-3">
      <div class="card summary-card h-100">
        <div class="card-body">
          <div class="card-label">Total Cost</div>
          <div class="card-value">{{ formatCost(totals?.estimated_cost_usd) }}</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card summary-card h-100">
        <div class="card-body">
          <div class="card-label">Total Tokens</div>
          <div class="card-value">{{ formatTokens(totalTokens) }}</div>
          <div class="card-sub">
            <span class="token-chip input">↓{{ formatTokens(totals?.input_tokens) }}</span>
            <span class="token-chip output">↑{{ formatTokens(totals?.output_tokens) }}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card summary-card h-100">
        <div class="card-body">
          <div class="card-label">Sessions</div>
          <div class="card-value">{{ totals?.session_count ?? '—' }}</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card summary-card h-100">
        <div class="card-body">
          <div class="card-label">Top Spender</div>
          <div class="card-value top-name" :title="totals?.top_session?.session_name">
            {{ totals?.top_session?.session_name || '—' }}
          </div>
          <div class="card-sub" v-if="totals?.top_session?.estimated_cost_usd != null">
            {{ formatCost(totals.top_session.estimated_cost_usd) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'
import { formatCost, formatTokens } from '@/utils/analytics'

const store = useAnalyticsStore()
const totals = computed(() => store.totals)
const totalTokens = computed(() => {
  if (!totals.value) return null
  return (totals.value.input_tokens || 0) + (totals.value.output_tokens || 0)
    + (totals.value.cache_write_tokens || 0) + (totals.value.cache_read_tokens || 0)
})
</script>

<style scoped>
.summary-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
}
.card-body {
  padding: 16px;
}
.card-label {
  font-size: 11px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}
.card-value {
  font-size: 22px;
  font-weight: 700;
  color: #f1f5f9;
  line-height: 1.2;
}
.top-name {
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.card-sub {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.token-chip {
  background: #0f172a;
  border-radius: 4px;
  padding: 1px 6px;
}
.token-chip.input { color: #818cf8; }
.token-chip.output { color: #4ade80; }
</style>
