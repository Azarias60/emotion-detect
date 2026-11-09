<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuthShell from '@/components/AuthShell.vue'
import { login } from '@/api/auth'
import { useUserStore } from '@/stores/user'

const router = useRouter(),
  route = useRoute(),
  store = useUserStore()
const form = reactive({ username: '', password: '' })
const loading = ref(false),
  error = ref('')
async function submit() {
  error.value = ''
  if (!form.username || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const response = await login(form)
    store.setSession(response.data.token, response.data.user)
    router.replace(route.query.redirect || '/home')
  } catch (reason) {
    error.value = reason.response?.data?.detail || reason.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <AuthShell title="用户登录" subtitle="USER AUTHENTICATION"
    ><form class="auth-form" @submit.prevent="submit">
      <div class="auth-field">
        <label>用户名</label
        ><input
          v-model.trim="form.username"
          maxlength="16"
          autocomplete="username"
          placeholder="请输入用户名"
        />
      </div>
      <div class="auth-field">
        <label>密码</label
        ><input
          v-model="form.password"
          type="password"
          maxlength="64"
          autocomplete="current-password"
          placeholder="请输入密码"
        />
      </div>
      <p v-if="error" class="form-error">{{ error }}</p>
      <button class="auth-submit" :disabled="loading">{{ loading ? '登录中...' : '登 录' }}</button>
    </form>
    <p class="auth-switch">
      还没有账号？ <RouterLink to="/register">立即注册</RouterLink>
    </p></AuthShell
  >
</template>
