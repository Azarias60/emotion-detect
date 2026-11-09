<script setup>
import HardwareModeSwitch from './HardwareModeSwitch.vue'

const items = [
  { path: '/', label: '检测主页', icon: '⌂' },
  { path: '/detect/health', label: '健康问诊', icon: '✚' },
  { path: '/detect/fatigue', label: '疲劳检测', icon: '◉' },
  { path: '/detect/emotion', label: '情绪识别', icon: '◇' },
  { path: '/detect/companion', label: '情感数字人', icon: '◎' },
  { path: '/management', label: '健康管理', icon: '▦' },
]
</script>

<template>
  <nav class="detection-nav" aria-label="检测页面导航">
    <RouterLink v-for="item in items" :key="item.path" :to="item.path" class="nav-item">
      <span class="nav-icon">{{ item.icon }}</span>
      <span>{{ item.label }}</span>
    </RouterLink>
    <div class="nav-divider"></div>
    <HardwareModeSwitch />
  </nav>
</template>

<style scoped>
.detection-nav {
  display: flex;
  gap: 8px;
  align-items: center;
}
.nav-divider {
  width: 1px;
  height: 22px;
  background: rgba(0, 242, 255, 0.25);
  margin: 0 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 13px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #83cbd2;
  text-decoration: none;
  background: rgba(0, 31, 48, 0.75);
  font-size: 13px;
  letter-spacing: 1px;
  transition: 0.2s;
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
}
.nav-item:hover {
  color: #fff;
  border-color: #00f2ff;
  box-shadow: inset 0 0 14px rgba(0, 242, 255, 0.12);
}
.nav-item.router-link-active {
  color: #00f2ff;
  border-color: #00f2ff;
  background: rgba(0, 242, 255, 0.13);
  box-shadow: 0 0 12px rgba(0, 242, 255, 0.18);
}
.nav-icon {
  color: #ff9d2e;
  font-size: 15px;
}
@media (max-width: 1050px) {
  .nav-item {
    padding: 7px 8px;
    font-size: 12px;
  }
  .nav-icon {
    display: none;
  }
  .nav-divider {
    display: none;
  }
}
</style>
