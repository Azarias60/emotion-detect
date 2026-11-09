<script setup>
import { reactive, ref } from 'vue'
import { updatePassword } from '@/api/user'
const form = reactive({ password: '', newPassword: '', newRePassword: '' }),
  loading = ref(false),
  message = ref(''),
  error = ref('')
async function submit() {
  message.value = ''
  error.value = ''
  if (form.newPassword !== form.newRePassword) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  try {
    await updatePassword(form)
    message.value = '密码修改成功'
    Object.assign(form, { password: '', newPassword: '', newRePassword: '' })
  } catch (reason) {
    error.value = reason.response?.data?.detail || '密码修改失败'
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>安全设置</h1>
        <p>定期修改密码可以提高账号安全性</p>
      </div>
    </div>
    <section class="manage-panel password-panel">
      <div class="manage-panel-title">修改登录密码</div>
      <form @submit.prevent="submit">
        <label
          ><span>当前密码</span
          ><input v-model="form.password" class="manage-input" type="password" required /></label
        ><label
          ><span>新密码</span
          ><input
            v-model="form.newPassword"
            class="manage-input"
            type="password"
            minlength="5"
            required /></label
        ><label
          ><span>确认新密码</span
          ><input
            v-model="form.newRePassword"
            class="manage-input"
            type="password"
            minlength="5"
            required
        /></label>
        <p v-if="message" class="success">{{ message }}</p>
        <p v-if="error" class="manage-error">{{ error }}</p>
        <button class="manage-btn" :disabled="loading">
          {{ loading ? '提交中...' : '修改密码' }}
        </button>
      </form>
    </section>
  </div>
</template>
<style scoped>
.password-panel {
  max-width: 650px;
}
.password-panel form {
  padding: 28px;
  display: grid;
  gap: 17px;
}
.password-panel label {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
}
.password-panel label span {
  color: #72959b;
  font-size: 12px;
}
.password-panel button {
  justify-self: start;
}
.success {
  color: #00d59f;
  font-size: 12px;
}
</style>
