import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/utils/api'
import { presetToRange, selectBucketSize, formatBucketLabel, TIME_PRESETS, readCssVar } from '@/utils/analytics'
import { useUIStore } from '@/stores/ui'

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
        { key: 'input_tokens',       label: 'Input',       color: readCssVar('--chart-color-input') },
        { key: 'output_tokens',      label: 'Output',      color: readCssVar('--chart-color-output') },
        { key: 'cache_write_tokens', label: 'Cache Write', color: readCssVar('--chart-color-cache-write') },
        { key: 'cache_read_tokens',  label: 'Cache Read',  color: readCssVar('--chart-color-cache-read') },
      ]
      if (metric === 'cost') {
        return {
          datasets: [{
            label: 'Estimated Cost (USD)',
            data: bkts.map(b => ({ x: b._ts_ms, y: b.by_token_type.estimated_cost_usd || 0 })),
            backgroundColor: readCssVar('--chart-color-cost'),
            borderColor:     readCssVar('--chart-color-cost-border'),
            borderWidth: 1,
          }],
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
      return {
        label: modelName || '(unknown)',
        data: bkts.map(b => {
          const entry = b.by_model.find(m => m.model === modelName)
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
  }

  function setSessionSearch(q) {
    filters.value.sessionSearch = q
  }

  function setChartGrouping(g) {
    filters.value.chartGrouping = g
  }

  function setChartMetric(m) {
    filters.value.chartMetric = m
  }

  async function fetchData() {
    loading.value = true
    error.value = null

    const { since, until } = _effectiveRange()
    const bucketSize = selectBucketSize(since, until)
    groupBy.value = bucketSize

    const baseParams = { since, until }

    try {
      const [sessionResp, timeResp] = await Promise.all([
        api.get('/api/analytics/usage', { params: { ...baseParams, group_by: 'session' } }),
        api.get('/api/analytics/usage', { params: { ...baseParams, group_by: bucketSize } }),
      ])

      sessionRows.value = sessionResp.rows || []
      totals.value = sessionResp.totals || null

      buckets.value = (timeResp.buckets || []).map(b => ({
        ...b,
        _ts_ms: b.bucket_ts * 1000,
        _label: formatBucketLabel(b.bucket_ts, bucketSize),
      }))
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
