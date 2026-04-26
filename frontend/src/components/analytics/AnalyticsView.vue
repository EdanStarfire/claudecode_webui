<template>
  <div class="analytics-view">
    <div class="analytics-header">
      <h2 class="analytics-title">Analytics</h2>
    </div>

    <div class="analytics-body container-fluid px-3 py-3">
      <AnalyticsFilters class="mb-3" />

      <div v-if="store.loading" class="loading-state text-center py-5 text-muted">
        Loading usage data…
      </div>

      <template v-else-if="store.error">
        <div class="alert alert-danger">{{ store.error }}</div>
      </template>

      <template v-else-if="hasData">
        <AnalyticsSummaryCards />

        <!-- Chart: hidden on mobile -->
        <div class="d-none d-md-block">
          <AnalyticsTimeSeriesChart />
        </div>

        <AnalyticsSessionTable />
      </template>

      <AnalyticsEmptyState v-else />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'
import AnalyticsFilters from './AnalyticsFilters.vue'
import AnalyticsSummaryCards from './AnalyticsSummaryCards.vue'
import AnalyticsTimeSeriesChart from './AnalyticsTimeSeriesChart.vue'
import AnalyticsSessionTable from './AnalyticsSessionTable.vue'
import AnalyticsEmptyState from './AnalyticsEmptyState.vue'

const store = useAnalyticsStore()

const hasData = computed(() =>
  store.sessionRows.length > 0 || store.buckets.length > 0
)

onMounted(() => {
  store.fetchData()
})
</script>

<style scoped>
.analytics-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: #0f172a;
}
.analytics-header {
  background: #1e293b;
  border-bottom: 1px solid #334155;
  padding: 12px 16px;
  flex-shrink: 0;
}
.analytics-title {
  font-size: 16px;
  font-weight: 600;
  color: #f1f5f9;
  margin: 0;
}
.analytics-body {
  flex: 1;
  overflow-y: auto;
}
</style>
