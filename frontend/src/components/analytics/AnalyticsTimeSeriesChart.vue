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

const store = useAnalyticsStore()
const chartReady = ref(false)
let Bar = null

onMounted(async () => {
  try {
    const { Bar: BarComponent } = await import('vue-chartjs')
    const { Chart, registerables } = await import('chart.js')
    Chart.register(...registerables)
    Bar = BarComponent
    chartReady.value = true
  } catch {
    chartReady.value = false
  }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      labels: { color: '#94a3b8', font: { size: 11 } },
    },
    tooltip: { mode: 'index', intersect: false },
  },
  scales: {
    x: {
      stacked: true,
      ticks: { color: '#64748b', maxRotation: 45, font: { size: 10 } },
      grid: { color: '#1e293b' },
    },
    y: {
      stacked: true,
      ticks: { color: '#64748b', font: { size: 10 } },
      grid: { color: '#334155' },
      title: {
        display: true,
        text: store.filters.chartMetric === 'cost' ? 'USD' : 'Tokens',
        color: '#64748b',
        font: { size: 10 },
      },
    },
  },
}))
</script>

<style scoped>
.chart-panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.chart-label {
  font-size: 11px;
  color: #64748b;
}
.chart-container {
  height: 260px;
  position: relative;
}
.chart-fallback {
  padding: 32px;
  text-align: center;
  color: #64748b;
  font-size: 13px;
}
</style>
