<script setup lang="ts">
// 顶部线性进度条：任何异步操作期间显示
defineProps<{ active: boolean }>()
</script>

<template>
  <div v-if="active" class="loading-bar" role="status" aria-label="加载中">
    <div class="bar"></div>
  </div>
</template>

<style scoped>
.loading-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  z-index: 100;
  background: rgba(47, 95, 208, 0.12);
  overflow: hidden;
}

.bar {
  height: 100%;
  width: 40%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--brand), var(--brand-accent));
  animation: slide 1s ease-in-out infinite;
}

@keyframes slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(350%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .bar {
    animation: none;
    width: 100%;
    opacity: 0.6;
  }
}
</style>
