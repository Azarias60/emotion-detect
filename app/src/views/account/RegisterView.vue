<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AuthShell from '@/components/AuthShell.vue'
import { register } from '@/api/auth'
const router = useRouter(),
  form = reactive({ username: '', password: '', rePassword: '' }),
  loading = ref(false),
  error = ref('')
async function submit() {
  error.value = ''
  if (form.password !== form.rePassword) {
    error.value = '两次密码输入不一致'
    return
  }
  loading.value = true
  try {
    await register(form)
    router.push('/login')
  } catch (reason) {
    error.value = reason.response?.data?.detail || reason.message || '注册失败'
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <AuthShell title="创建账号" subtitle="CREATE A HEALTH MANAGEMENT ACCOUNT"
    ><form class="auth-form" @submit.prevent="submit">
      <div class="auth-field">
        <label>用户名</label
        ><input
          v-model.trim="form.username"
          minlength="3"
          maxlength="16"
          placeholder="3-16个字符"
        />
      </div>
      <div class="auth-field">
        <label>密码</label
        ><input
          v-model="form.password"
          type="password"
          minlength="5"
          maxlength="64"
          placeholder="至少5个字符"
        />
      </div>
      <div class="auth-field">
        <label>确认密码</label
        ><input
          v-model="form.rePassword"
          type="password"
          minlength="5"
          maxlength="64"
          placeholder="再次输入密码"
        />
      </div>
      <p v-if="error" class="form-error">{{ error }}</p>
      <button class="auth-submit" :disabled="loading">{{ loading ? '注册中...' : '注 册' }}</button>
    </form>
    <p class="auth-switch">已有账号？ <RouterLink to="/login">返回登录</RouterLink></p></AuthShell
  >
</template>
