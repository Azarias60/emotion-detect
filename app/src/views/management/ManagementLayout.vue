<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { logout } from '@/api/auth'
import { getUserInfo } from '@/api/user'
import { useUserStore } from '@/stores/user'
const store = useUserStore(),
  router = useRouter()
const avatar = computed(() => {
  const value = store.user?.userPic || ''
  return value.startsWith('/uploads/') ? `/python-api${value}` : value
})
const nav = [
  { to: '/management/dashboard', icon: '▦', label: '管理概览' },
  { to: '/management/reports', icon: '▤', label: '检测报告' },
  { to: '/management/analysis', icon: '⌁', label: '综合分析' },
  { to: '/management/profile', icon: '◉', label: '个人资料' },
  { to: '/management/security', icon: '◇', label: '安全设置' },
]
onMounted(async () => {
  try {
    const response = await getUserInfo()
    store.setUser(response.data)
  } catch {
    /* interceptor handles auth */
  }
})
async function signOut() {
  try {
    await logout()
  } finally {
    store.clearSession()
    router.replace('/login')
  }
}
</script>
<template>
  <div class="manage-screen">
    <aside class="manage-side">
      <div class="manage-logo">
        <span>CUG</span>
        <div><strong>健康管理中心</strong><small>MANAGEMENT</small></div>
      </div>
      <nav>
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          ><span>{{ item.icon }}</span
          >{{ item.label }}</RouterLink
        >
      </nav>
      <div class="side-bottom">
        <RouterLink to="/detect/emotion">← 返回检测系统</RouterLink
        ><button @click="signOut">退出登录</button>
      </div>
    </aside>
    <section class="manage-main">
      <header class="manage-top">
        <div><span class="system-dot"></span>多模态健康数据平台</div>
        <div class="top-user">
          <div class="top-avatar">
            <img v-if="avatar" :src="avatar" /><span v-else>{{
              store.user?.username?.slice(0, 1)?.toUpperCase() || 'U'
            }}</span>
          </div>
          <div>
            <strong>{{ store.user?.username || '用户' }}</strong
            ><small>在线</small>
          </div>
        </div>
      </header>
      <main class="manage-content"><RouterView /></main>
    </section>
  </div>
</template>
<style>
:root {
  font-family: 'Microsoft YaHei', sans-serif;
  background: #000b13;
  color: #c8e6ea;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
.manage-screen {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 230px 1fr;
  background: radial-gradient(circle at 70% 0, rgba(0, 102, 132, 0.13), transparent 35%), #000b13;
}
.manage-side {
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 1px solid rgba(0, 242, 255, 0.18);
  background: rgba(0, 16, 27, 0.94);
  display: flex;
  flex-direction: column;
  padding: 20px 14px;
}
.manage-logo {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 3px 7px 24px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.12);
}
.manage-logo > span {
  width: 39px;
  height: 39px;
  border: 1px solid #00f2ff;
  display: grid;
  place-items: center;
  color: #00f2ff;
  font-size: 11px;
  font-weight: 800;
}
.manage-logo div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.manage-logo strong {
  color: #b9f4f7;
  font-size: 14px;
  letter-spacing: 1px;
}
.manage-logo small {
  color: #436c75;
  font-size: 9px;
  letter-spacing: 2px;
}
.manage-side nav {
  display: grid;
  gap: 7px;
  margin-top: 24px;
}
.manage-side nav a {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  color: #6f98a0;
  text-decoration: none;
  border: 1px solid transparent;
  font-size: 13px;
}
.manage-side nav a span {
  color: #ff9d2e;
  width: 20px;
  text-align: center;
}
.manage-side nav a:hover,
.manage-side nav a.router-link-active {
  color: #00f2ff;
  border-color: rgba(0, 242, 255, 0.26);
  background: rgba(0, 242, 255, 0.07);
  box-shadow: inset 2px 0 #00f2ff;
}
.side-bottom {
  margin-top: auto;
  display: grid;
  gap: 8px;
}
.side-bottom a,
.side-bottom button {
  border: 1px solid rgba(0, 242, 255, 0.12);
  background: transparent;
  color: #587d84;
  padding: 10px;
  text-align: center;
  text-decoration: none;
  font-size: 11px;
  cursor: pointer;
}
.side-bottom button {
  border-color: rgba(255, 107, 84, 0.16);
  color: #a06b64;
}
.manage-main {
  min-width: 0;
}
.manage-top {
  height: 68px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.14);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 25px;
  color: #69929a;
  font-size: 12px;
}
.system-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  background: #00efbc;
  border-radius: 50%;
  box-shadow: 0 0 8px #00efbc;
  margin-right: 8px;
}
.top-user {
  display: flex;
  align-items: center;
  gap: 9px;
}
.top-user > div:last-child {
  display: flex;
  flex-direction: column;
}
.top-user strong {
  color: #b9dce0;
  font-size: 12px;
}
.top-user small {
  color: #00c993;
  font-size: 9px;
}
.top-avatar {
  width: 35px;
  height: 35px;
  border: 1px solid rgba(0, 242, 255, 0.35);
  display: grid;
  place-items: center;
  color: #00f2ff;
  overflow: hidden;
}
.top-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.manage-content {
  padding: 24px;
  max-width: 1500px;
  margin: auto;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: end;
  margin-bottom: 20px;
}
.page-head h1 {
  margin: 0;
  color: #00f2ff;
  font-size: 22px;
  letter-spacing: 3px;
}
.page-head p {
  margin: 6px 0 0;
  color: #537780;
  font-size: 11px;
}
.manage-panel {
  border: 1px solid rgba(0, 242, 255, 0.2);
  background: rgba(0, 22, 34, 0.76);
  position: relative;
}
.manage-panel::before {
  content: '';
  position: absolute;
  left: 18px;
  top: -1px;
  width: 65px;
  height: 2px;
  background: #00f2ff;
}
.manage-panel-title {
  padding: 13px 16px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.12);
  color: #8fdde2;
  font-size: 13px;
  letter-spacing: 1px;
}
.manage-btn {
  padding: 9px 16px;
  border: 1px solid #00bfd0;
  background: rgba(0, 242, 255, 0.09);
  color: #00f2ff;
  cursor: pointer;
}
.manage-btn.warn {
  border-color: #9f493d;
  color: #ff7666;
  background: rgba(255, 84, 67, 0.06);
}
.manage-btn:disabled {
  opacity: 0.45;
}
.manage-input {
  padding: 9px 11px;
  border: 1px solid rgba(0, 242, 255, 0.2);
  background: #00101a;
  color: #c9e8eb;
  outline: none;
}
.manage-input:focus {
  border-color: #00dbe9;
}
.manage-empty {
  padding: 55px;
  text-align: center;
  color: #4f737b;
}
.manage-error {
  color: #ff736a;
  font-size: 12px;
}
@media (max-width: 850px) {
  .manage-screen {
    grid-template-columns: 1fr;
  }
  .manage-side {
    height: auto;
    position: relative;
  }
  .manage-side nav {
    grid-template-columns: repeat(2, 1fr);
  }
  .side-bottom {
    margin-top: 20px;
  }
  .manage-content {
    padding: 14px;
  }
}
</style>
