<template>
  <div class="session-table-panel">
    <div class="table-responsive">
      <table class="table table-dark table-sm table-hover analytics-table mb-0">
        <thead>
          <tr>
            <th @click="setSort('session_name')" class="sortable">
              Session <span class="sort-icon">{{ sortIcon('session_name') }}</span>
            </th>
            <th @click="setSort('model')" class="sortable">
              Model <span class="sort-icon">{{ sortIcon('model') }}</span>
            </th>
            <th @click="setSort('turn_count')" class="sortable text-end">
              Turns <span class="sort-icon">{{ sortIcon('turn_count') }}</span>
            </th>
            <th @click="setSort('input_tokens')" class="sortable text-end">
              Input <span class="sort-icon">{{ sortIcon('input_tokens') }}</span>
            </th>
            <th @click="setSort('output_tokens')" class="sortable text-end">
              Output <span class="sort-icon">{{ sortIcon('output_tokens') }}</span>
            </th>
            <th @click="setSort('cache_read_tokens')" class="sortable text-end d-none d-md-table-cell">
              Cache Read <span class="sort-icon">{{ sortIcon('cache_read_tokens') }}</span>
            </th>
            <th @click="setSort('estimated_cost_usd')" class="sortable text-end">
              Est. Cost <span class="sort-icon">{{ sortIcon('estimated_cost_usd') }}</span>
            </th>
            <th @click="setSort('last_active')" class="sortable text-end d-none d-md-table-cell">
              Last Active <span class="sort-icon">{{ sortIcon('last_active') }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedRows" :key="row.session_id" :class="{ 'minion-row': row.is_minion }">
            <td>
              <span v-if="row.is_minion" class="minion-indent">↳</span>
              <span class="session-name" :title="row.session_name">{{ row.session_name || row.session_id.slice(0, 8) }}</span>
              <span v-if="row.is_minion" class="badge bg-secondary ms-1" title="Minion session">minion</span>
            </td>
            <td class="model-cell">{{ row.model || '—' }}</td>
            <td class="text-end">{{ row.turn_count }}</td>
            <td class="text-end">{{ formatTokens(row.input_tokens) }}</td>
            <td class="text-end">{{ formatTokens(row.output_tokens) }}</td>
            <td class="text-end d-none d-md-table-cell">{{ formatTokens(row.cache_read_tokens) }}</td>
            <td class="text-end cost-cell">
              <span :class="{ 'unknown-rate': !row.rates_known }">
                {{ formatCost(row.estimated_cost_usd) }}
              </span>
            </td>
            <td class="text-end d-none d-md-table-cell text-muted small">
              {{ formatLastActive(row.last_active) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'
import { formatCost, formatTokens } from '@/utils/analytics'

const store = useAnalyticsStore()

const sortKey = ref('estimated_cost_usd')
const sortAsc = ref(false)

function setSort(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = key === 'session_name' || key === 'model'
  }
}

function sortIcon(key) {
  if (sortKey.value !== key) return ''
  return sortAsc.value ? '↑' : '↓'
}

const sortedRows = computed(() => {
  const rows = [...store.filteredSessionRows]
  const key = sortKey.value
  const dir = sortAsc.value ? 1 : -1
  return rows.sort((a, b) => {
    const av = a[key] ?? ''
    const bv = b[key] ?? ''
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir
    return String(av).localeCompare(String(bv)) * dir
  })
})

function formatLastActive(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
</script>

<style scoped>
.session-table-panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  overflow: hidden;
}
.analytics-table {
  --bs-table-bg: transparent;
  --bs-table-hover-bg: rgba(99,102,241,0.07);
  font-size: 12px;
  margin: 0;
}
.analytics-table th {
  background: #0f172a;
  color: #94a3b8;
  font-size: 11px;
  font-weight: 500;
  border-bottom: 1px solid #334155;
  white-space: nowrap;
  padding: 8px 10px;
}
.analytics-table td {
  border-color: #1e293b;
  padding: 7px 10px;
  vertical-align: middle;
  color: #e2e8f0;
}
.sortable {
  cursor: pointer;
  user-select: none;
}
.sortable:hover {
  color: #c7d2fe;
}
.sort-icon {
  color: #6366f1;
  font-size: 10px;
}
.session-name {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  vertical-align: bottom;
}
.model-cell {
  color: #94a3b8;
  font-size: 11px;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.minion-row td:first-child {
  padding-left: 20px;
}
.minion-indent {
  color: #64748b;
  margin-right: 4px;
}
.cost-cell {
  font-weight: 600;
  color: #a5b4fc;
}
.unknown-rate {
  color: #64748b;
  font-weight: normal;
}
</style>
