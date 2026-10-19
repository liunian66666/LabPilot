<script setup lang="ts">
defineProps<{
  label: string
  value: number | string
  tone?: 'brand' | 'warn' | 'danger' | 'ok'
}>()
</script>

<template>
  <div class="stat-card" :class="tone ? `tone-${tone}` : 'tone-brand'">
    <div class="label">{{ label }}</div>
    <div class="value">{{ value }}</div>
  </div>
</template>

<style scoped>
.stat-card {
  position: relative;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px 18px 14px;
  overflow: hidden;
  box-shadow: var(--shadow);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.stat-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--accent, var(--brand));
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-lift);
}

.label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.06em;
}

.value {
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 700;
  margin-top: 2px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.tone-brand {
  --accent: var(--brand);
}

.tone-ok {
  --accent: var(--ok);
}

.tone-warn {
  --accent: var(--warn);
}

.tone-danger {
  --accent: var(--danger);
}
</style>
