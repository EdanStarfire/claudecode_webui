<template>
  <Transition name="jump-cue">
    <div v-if="cue" class="jump-cue" aria-hidden="true" data-testid="jump-cue">
      <div class="jump-cue-rail">
        <span class="jump-cue-trail" :style="trailStyle"></span>
        <span class="jump-cue-dot jump-cue-dot-from" :style="{ top: pct(cue.fromFrac) }"></span>
        <span class="jump-cue-dot jump-cue-dot-to" :style="toStyle"></span>
      </div>
      <span class="jump-cue-pill" :class="cue.up ? 'jump-cue-pill-up' : 'jump-cue-pill-down'" data-testid="jump-cue-pill">{{ cue.label }}</span>
    </div>
  </Transition>
</template>

<script setup>
import { computed } from 'vue'

// Issue #2342: direction + distance cue of a LONG jump (the list teleports next to the target and settles,
// so the move itself does not show how far it went): a right-edge rail on the whole-conversation scale
// (hollow dot = where the user was, solid dot = where they landed) and a pill naming direction, turns and
// percentage. Outside the scroll container, position:absolute and pointer-events:none: it never takes
// layout space or input. Reduced motion never sets a cue.
const props = defineProps({
  // { fromFrac, toFrac, up, label } with fractions in [0, 1] of the scrollable range, or null
  cue: { type: Object, default: null },
})

const pct = (frac) => `${(frac * 100).toFixed(2)}%`
const trailStyle = computed(() => {
  const lo = Math.min(props.cue.fromFrac, props.cue.toFrac)
  const hi = Math.max(props.cue.fromFrac, props.cue.toFrac)
  return { top: pct(lo), height: pct(hi - lo) }
})
const toStyle = computed(() => ({ top: pct(props.cue.toFrac), '--jump-cue-from': pct(props.cue.fromFrac) }))
</script>

<style scoped>
.jump-cue {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 5;
}

.jump-cue-rail {
  position: absolute;
  right: 3px;
  top: 14px;
  bottom: 14px;
  width: 8px;
  border-radius: 4px;
  background: var(--bs-secondary-bg, rgba(127, 127, 127, 0.18));
}

.jump-cue-trail {
  position: absolute;
  left: 1px;
  right: 1px;
  border-radius: 3px;
  background: var(--bs-primary, #0d6efd);
  opacity: 0.55;
}

.jump-cue-dot {
  position: absolute;
  left: -4px;
  width: 16px;
  height: 16px;
  margin-top: -8px;
  box-sizing: border-box;
  border-radius: 50%;
  border: 2px solid var(--bs-primary, #0d6efd);
  background: var(--bs-body-bg, #fff);
}

.jump-cue-dot-to {
  background: var(--bs-primary, #0d6efd);
  box-shadow: 0 0 8px var(--bs-primary, #0d6efd);
  animation: jump-cue-travel 0.32s cubic-bezier(0.2, 0.8, 0.2, 1);
}

@keyframes jump-cue-travel {
  from { top: var(--jump-cue-from); }
}

.jump-cue-pill {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 14px;
  border-radius: 999px;
  background: var(--bs-primary, #0d6efd);
  color: var(--bs-white, #fff);
  font-weight: 700;
  font-size: 0.85rem;
  white-space: nowrap;
}

.jump-cue-pill-up { top: calc(10px + env(safe-area-inset-top, 0px)); }
.jump-cue-pill-down { bottom: 10px; }

.jump-cue-enter-active { transition: opacity 0.2s; }
.jump-cue-leave-active { transition: opacity 0.25s; }
.jump-cue-enter-from,
.jump-cue-leave-to { opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .jump-cue-dot-to { animation: none; }
  .jump-cue-enter-active,
  .jump-cue-leave-active { transition: none; }
}
</style>
