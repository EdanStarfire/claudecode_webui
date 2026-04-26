<template>
  <div class="analytics-empty">
    <div class="empty-icon">📊</div>
    <h5 class="empty-title">No usage data</h5>
    <p class="empty-body">
      Start a session and send some messages — token usage will appear here once recorded.
    </p>
  </div>
</template>

<style scoped>
.analytics-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 24px;
  color: #94a3b8;
  text-align: center;
}
.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.5;
}
.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: #cbd5e1;
  margin: 0 0 8px;
}
.empty-body {
  font-size: 13px;
  max-width: 360px;
  margin: 0;
  line-height: 1.6;
}
</style>
