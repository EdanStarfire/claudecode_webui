<template>
  <div class="proxy-config-tab">
    <div class="alert alert-info small">
      <strong>Credentials moved to Secrets.</strong>
      Secret values and host-pattern credentials are now managed in the <strong>Secrets</strong> tab.
      Per-session proxy settings (enable, image, allowed domains, assigned secrets) remain in the
      session configuration form.
    </div>
  </div>
</template>

<script setup>
</script>

<style scoped>
.proxy-config-tab {
  min-height: 200px;
}
</style>
