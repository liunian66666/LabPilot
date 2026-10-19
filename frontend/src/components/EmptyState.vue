<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <div class="empty">
    <svg viewBox="0 0 48 48" width="36" height="36" aria-hidden="true">
      <rect x="8" y="10" width="32" height="28" rx="4" fill="none" stroke="currentColor" stroke-width="2" />
      <path d="M16 20h16M16 28h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
    </svg>
    <p>{{ text }}</p>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--muted);
  font-size: 13px;
  padding: 10px 2px;
}

.empty svg {
  opacity: 0.55;
  flex: none;
}

.empty p {
  margin: 0;
}
</style>
