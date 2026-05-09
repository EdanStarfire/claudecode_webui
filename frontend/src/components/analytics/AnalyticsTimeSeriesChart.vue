<template>
  <div class="chart-panel">
    <div class="chart-toolbar d-flex gap-2 align-items-center mb-2 flex-wrap">
      <span class="chart-label">Series:</span>
      <div class="btn-group btn-group-sm">
        <button
          class="btn"
          :class="store.filters.chartGrouping === 'token_type' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="store.setChartGrouping('token_type')"
        >By token type</button>
        <button
          class="btn"
          :class="store.filters.chartGrouping === 'model' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="store.setChartGrouping('model')"
        >By model</button>
      </div>
      <span class="chart-label ms-2">Y axis:</span>
      <div class="btn-group btn-group-sm">
        <button
          class="btn"
          :class="store.filters.chartMetric === 'cost' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="store.setChartMetric('cost')"
        >Cost</button>
        <button
          class="btn"
          :class="store.filters.chartMetric === 'tokens' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="store.setChartMetric('tokens')"
        >Tokens</button>
      </div>
    </div>

    <div v-if="chartReady" class="chart-container">
      <Bar :data="store.chartSeries" :options="chartOptions" />
    </div>
    <div v-else class="chart-fallback">
      Chart unavailable — see the session table below for detailed usage data.
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'
import { useUIStore } from '@/stores/ui'
import { readCssVar } from '@/utils/analytics'

const store = useAnalyticsStore()
const uiStore = useUIStore()
const chartReady = ref(false)
let Bar = null

onMounted(async () => {
  try {
    const { Bar: BarComponent } = await import('vue-chartjs')
    const { Chart, registerables } = await import('chart.js')
    await import('chartjs-adapter-date-fns')
    Chart.register(...registerables)
    Bar = BarComponent
    chartReady.value = true
  } catch {
    chartReady.value = false
  }
})

const chartOptions = computed(() => {
  // eslint-disable-next-line no-unused-expressions
  uiStore.theme
  const tickColor  = readCssVar('--bs-secondary-color')
  const gridColor  = readCssVar('--bs-border-color')

  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: { color: tickColor, font: { size: 11 } },
      },
      tooltip: { mode: 'index', intersect: false },
    },
    scales: {
      x: {
        type: 'time',
        stacked: true,
        time: {
          unit: store.timeUnit,
          tooltipFormat: store.timeUnit === 'hour' ? 'MMM d, HH:mm' : 'MMM d',
          displayFormats: {
            hour: 'MMM d HH:mm',
            day: 'MMM d',
          },
        },
        ticks: {
          color: tickColor,
          maxRotation: 45,
          autoSkip: true,
          autoSkipPadding: 8,
          font: { size: 10 },
        },
        grid: { color: gridColor },
      },
      y: {
        stacked: true,
        ticks: { color: tickColor, font: { size: 10 } },
        grid: { color: gridColor },
        title: {
          display: true,
          text: store.filters.chartMetric === 'cost' ? 'USD' : 'Tokens',
          color: tickColor,
          font: { size: 10 },
        },
      },
    },
  }
})
</script>

<style scoped>
.chart-panel {
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.chart-label {
  font-size: 11px;
  color: var(--bs-secondary-color);
}
.chart-container {
  height: 260px;
  position: relative;
}
.chart-fallback {
  padding: 32px;
  text-align: center;
  color: var(--bs-secondary-color);
  font-size: 13px;
}
</style>
