import { createRouter, createWebHistory } from 'vue-router'

// 核心检测与展示视图
import HomeView from '@/features/guide/HomeView.vue'
import HealthDetect from '@/views/detect/HealthDetect.vue'
import FatigueDetect from '@/views/detect/FatigueDetect.vue'
import EmotionDetect from '@/views/detect/EmotionDetect.vue'
import CompanionDetect from '@/features/companion/CompanionDetect.vue'
import LockedView from '@/views/LockedView.vue'

// 账号与管理端视图
import LoginView from '@/views/account/LoginView.vue'
import RegisterView from '@/views/account/RegisterView.vue'
import ManagementLayout from '@/views/management/ManagementLayout.vue'
import DashboardView from '@/views/management/DashboardView.vue'
import ProfileView from '@/views/management/ProfileView.vue'
import PasswordView from '@/views/management/PasswordView.vue'
import ReportListView from '@/views/management/ReportListView.vue'
import ReportDetailView from '@/views/management/ReportDetailView.vue'
import AnalysisView from '@/views/management/AnalysisView.vue'

const routes = [
  // 首页与检测模块（支持直接免登录体验）
  { path: '/', component: HomeView },
  { path: '/home', component: HomeView },
  { path: '/detect', redirect: '/home' },
  { path: '/detect/health', component: HealthDetect },
  { path: '/detect/fatigue', component: FatigueDetect },
  { path: '/detect/emotion', component: EmotionDetect },
  { path: '/detect/companion', component: CompanionDetect },

  // 单实例锁定页：被另一个窗口顶掉后整页跳到这里（见 composables/useSingleInstance.js）
  { path: '/locked', component: LockedView },

  // 账号登录与注册
  { path: '/login', component: LoginView, meta: { guestOnly: true } },
  { path: '/register', component: RegisterView, meta: { guestOnly: true } },

  // 管理后台（需要登录凭证）
  {
    path: '/management',
    component: ManagementLayout,
    meta: { requiresAuth: true },
    redirect: '/management/dashboard',
    children: [
      { path: 'dashboard', component: DashboardView },
      { path: 'profile', component: ProfileView },
      { path: 'security', component: PasswordView },
      { path: 'reports', component: ReportListView },
      { path: 'reports/:id', component: ReportDetailView },
      { path: 'analysis', component: AnalysisView },
    ],
  },

  // 404 回退
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：保护管理后台，允许无障碍体验检测
router.beforeEach((to) => {
  const loggedIn = Boolean(localStorage.getItem('emotion-token'))
  if (to.matched.some((record) => record.meta.requiresAuth) && !loggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && loggedIn) {
    return '/home'
  }
})

export default router
