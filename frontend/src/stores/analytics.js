import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/utils/api'
import { presetToRange, selectBucketSize, formatBucketLabel, formatModelLabel, TIME_PRESETS, readCssVar } from '@/utils/analytics'
import { useUIStore } from '@/stores/ui'

const TOKEN_FIELDS = ['input_tokens', 'output_tokens', 'cache_write_tokens', 'cache_read_tokens']

/** Mirrors backend/analytics/aggregator.py's compute_session_totals() over an arbitrary row subset. */
export function computeFilteredTotals(rows) {
  const totals = {
    input_tokens: 0,
    output_tokens: 0,
    cache_write_tokens: 0,
    cache_read_tokens: 0,
    estimated_cost_usd: 0,
    session_count: rows.length,
    top_session: null,
  }
  let top = null
  for (const row of rows) {
    for (const f of TOKEN_FIELDS) {
      totals[f] += row[f] || 0
    }
    const cost = row.estimated_cost_usd || 0
    totals.estimated_cost_usd += cost
    if (top === null || cost > (top.estimated_cost_usd || 0)) {
      top = row
    }
  }
  if (top) {
    totals.top_session = {
      session_id: top.session_id,
      session_name: top.session_name,
      estimated_cost_usd: top.estimated_cost_usd,
    }
  }
  return totals
}

export const useAnalyticsStore = defineStore('analytics', () => {
  const uiStore = useUIStore()
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------
  const filters = ref({
    preset: '24h',
    since: null,    // Unix seconds (custom range only)
    until: null,
    models: [],     // selected model strings (empty = all)
    sessionSearch: '',
    chartGrouping: 'token_type',  // 'token_type' | 'model'
    chartMetric: 'cost',          // 'cost' | 'tokens'
  })

  const sessionRows = ref([])
  const buckets = ref([])
  const groupBy = ref('day')
  const totals = ref(null)
  const loading = ref(false)
  const error = ref(null)

  // -------------------------------------------------------------------------
  // Computed
  // -------------------------------------------------------------------------

  /** Session rows filtered by client-side search and model filter. */
  const filteredSessionRows = computed(() => {
    let rows = sessionRows.value
    const search = filters.value.sessionSearch.trim().toLowerCase()
    if (search) {
      rows = rows.filter(r => (r.session_name || '').toLowerCase().includes(search))
    }
    const models = filters.value.models
    if (models.length > 0) {
      rows = rows.filter(r => models.includes(r.model))
    }
    return rows
  })

  /** Summary totals scoped to the current filter (mirrors backend compute_session_totals). */
  const filteredTotals = computed(() => computeFilteredTotals(filteredSessionRows.value))

  /** Whether a session-search or model filter is currently active. */
  const hasActiveFilter = computed(() => (
    filters.value.sessionSearch.trim() !== '' || filters.value.models.length > 0
  ))

  /** All model names appearing in session rows (for filter dropdown). */
  const availableModels = computed(() => {
    const set = new Set(sessionRows.value.map(r => r.model).filter(Boolean))
    return [...set].sort()
  })

  /** Chart series derived from buckets and current grouping/metric. */
  const chartSeries = computed(() => {
    // eslint-disable-next-line no-unused-expressions
    uiStore.theme

    if (!buckets.value.length) return { datasets: [] }

    const grouping = filters.value.chartGrouping
    const metric = filters.value.chartMetric
    const bkts = buckets.value

    if (grouping === 'token_type') {
      const tokenTypes = [
        { key: 'input_tokens',       costKey: 'input_cost_usd',       label: 'Input',       color: readCssVar('--chart-color-input') },
        { key: 'output_tokens',      costKey: 'output_cost_usd',      label: 'Output',      color: readCssVar('--chart-color-output') },
        { key: 'cache_write_tokens', costKey: 'cache_write_cost_usd', label: 'Cache Write', color: readCssVar('--chart-color-cache-write') },
        { key: 'cache_read_tokens',  costKey: 'cache_read_cost_usd',  label: 'Cache Read',  color: readCssVar('--chart-color-cache-read') },
      ]
      if (metric === 'cost') {
        return {
          datasets: tokenTypes.map(tt => ({
            label: tt.label,
            data: bkts.map(b => ({ x: b._ts_ms, y: b.by_token_type[tt.costKey] || 0 })),
            backgroundColor: tt.color,
            borderWidth: 1,
          })),
        }
      }
      return {
        datasets: tokenTypes.map(tt => ({
          label: tt.label,
          data: bkts.map(b => ({ x: b._ts_ms, y: b.by_token_type[tt.key] || 0 })),
          backgroundColor: tt.color,
          borderWidth: 1,
        })),
      }
    }

    // grouping === 'model'
    const allModels = new Set()
    bkts.forEach(b => b.by_model.forEach(m => allModels.add(m.model)))
    const modelColors = [
      readCssVar('--chart-model-0'),
      readCssVar('--chart-model-1'),
      readCssVar('--chart-model-2'),
      readCssVar('--chart-model-3'),
      readCssVar('--chart-model-4'),
      readCssVar('--chart-model-5'),
    ]
    let colorIdx = 0
    const datasets = [...allModels].map(modelName => {
      const color = modelColors[colorIdx++ % modelColors.length]
      const entries = bkts.map(b => b.by_model.find(m => m.model === modelName))
      const ratesKnown = entries.find(e => e)?.rates_known
      return {
        label: formatModelLabel(modelName, ratesKnown),
        data: bkts.map((b, i) => {
          const entry = entries[i]
          const v = !entry
            ? 0
            : metric === 'cost'
              ? (entry.estimated_cost_usd || 0)
              : ((entry.input_tokens || 0) + (entry.output_tokens || 0))
          return { x: b._ts_ms, y: v }
        }),
        backgroundColor: color,
        borderWidth: 1,
      }
    })
    return { datasets }
  })

  const timeUnit = computed(() => groupBy.value)

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  let searchDebounceTimer = null
  let bucketRequestSeq = 0

  function _effectiveRange() {
    if (filters.value.since && filters.value.until) {
      return { since: filters.value.since, until: filters.value.until }
    }
    return presetToRange(filters.value.preset) || presetToRange('24h')
  }

  function setPreset(preset) {
    filters.value.preset = preset
    filters.value.since = null
    filters.value.until = null
    refresh()
  }

  function setCustomRange(since, until) {
    filters.value.since = since
    filters.value.until = until
    filters.value.preset = 'custom'
  }

  function setModelFilter(models) {
    filters.value.models = models
    _fetchBuckets()
  }

  function setSessionSearch(q) {
    filters.value.sessionSearch = q
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer)
    }
    searchDebounceTimer = setTimeout(() => {
      searchDebounceTimer = null
      _fetchBuckets()
    }, 250)
  }

  function setChartGrouping(g) {
    filters.value.chartGrouping = g
  }

  function setChartMetric(m) {
    filters.value.chartMetric = m
  }

  /** Fetches the time-bucket series, scoped to the active session/model filter if any. */
  async function _fetchBuckets() {
    const seq = ++bucketRequestSeq
    error.value = null

    const { since, until } = _effectiveRange()
    const bucketSize = selectBucketSize(since, until)
    groupBy.value = bucketSize

    if (hasActiveFilter.value && filteredSessionRows.value.length === 0) {
      buckets.value = []
      return
    }

    const params = { since, until, group_by: bucketSize }
    if (hasActiveFilter.value) {
      params.session_ids = filteredSessionRows.value.map(r => r.session_id).join(',')
    }

    try {
      const timeResp = await api.get('/api/analytics/usage', { params })
      if (seq !== bucketRequestSeq) return // stale response, a newer request superseded this one

      buckets.value = (timeResp.buckets || []).map(b => ({
        ...b,
        _ts_ms: b.bucket_ts * 1000,
        _label: formatBucketLabel(b.bucket_ts, bucketSize),
      }))
    } catch (e) {
      if (seq !== bucketRequestSeq) return
      error.value = e?.message || 'Failed to load analytics data'
    }
  }

  async function fetchData() {
    loading.value = true
    error.value = null

    const { since, until } = _effectiveRange()

    try {
      const sessionResp = await api.get('/api/analytics/usage', { params: { since, until, group_by: 'session' } })

      sessionRows.value = sessionResp.rows || []
      totals.value = sessionResp.totals || null

      await _fetchBuckets()
    } catch (e) {
      error.value = e?.message || 'Failed to load analytics data'
    } finally {
      loading.value = false
    }
  }

  async function refresh() {
    await fetchData()
  }

  return {
    filters,
    sessionRows,
    buckets,
    totals,
    loading,
    error,
    filteredSessionRows,
    filteredTotals,
    hasActiveFilter,
    availableModels,
    chartSeries,
    timeUnit,
    setPreset,
    setCustomRange,
    setModelFilter,
    setSessionSearch,
    setChartGrouping,
    setChartMetric,
    fetchData,
    refresh,
    TIME_PRESETS,
  }
})
