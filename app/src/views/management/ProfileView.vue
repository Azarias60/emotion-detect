<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { getUserInfo, updateUser, uploadAvatar } from '@/api/user'
import { useUserStore } from '@/stores/user'
const store = useUserStore(),
  loading = ref(false),
  message = ref(''),
  error = ref('')
const form = reactive({ username: '', gender: '', age: null, email: '', userPic: '' })
const avatar = computed(() =>
  form.userPic?.startsWith('/uploads/') ? `/python-api${form.userPic}` : form.userPic
)
function fill(user) {
  Object.assign(form, {
    username: user.username || '',
    gender: user.gender || '',
    age: user.age,
    email: user.email || '',
    userPic: user.userPic || '',
  })
}
onMounted(async () => {
  const response = await getUserInfo()
  fill(response.data)
  store.setUser(response.data)
})
async function chooseAvatar(event) {
  const file = event.target.files?.[0]
  if (!file) return
  error.value = ''
  try {
    const response = await uploadAvatar(file)
    form.userPic = response.data.url
  } catch (reason) {
    error.value = reason.response?.data?.detail || '头像上传失败'
  }
  event.target.value = ''
}
async function submit() {
  loading.value = true
  message.value = ''
  error.value = ''
  try {
    const response = await updateUser(form)
    store.setUser(response.data)
    fill(response.data)
    message.value = '资料更新成功'
  } catch (reason) {
    error.value = reason.response?.data?.detail || '更新失败'
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>个人资料</h1>
        <p>维护账号基础信息和个人头像</p>
      </div>
    </div>
    <section class="manage-panel profile-panel">
      <div class="manage-panel-title">账号信息</div>
      <div class="profile-body">
        <aside>
          <div class="large-avatar">
            <img v-if="avatar" :src="avatar" /><span v-else>{{
              form.username?.slice(0, 1)?.toUpperCase() || 'U'
            }}</span>
          </div>
          <label class="manage-btn upload-btn"
            >上传头像<input
              type="file"
              hidden
              accept="image/jpeg,image/png,image/webp"
              @change="chooseAvatar" /></label
          ><small>支持 JPG、PNG、WEBP，最大5MB</small>
        </aside>
        <form @submit.prevent="submit">
          <div class="form-grid">
            <label
              ><span>用户名</span
              ><input
                v-model.trim="form.username"
                class="manage-input"
                maxlength="16"
                required /></label
            ><label
              ><span>性别</span
              ><select v-model="form.gender" class="manage-input">
                <option value="">未设置</option>
                <option value="男">男</option>
                <option value="女">女</option>
                <option value="其他">其他</option>
              </select></label
            ><label
              ><span>年龄</span
              ><input
                v-model.number="form.age"
                class="manage-input"
                type="number"
                min="1"
                max="120" /></label
            ><label
              ><span>邮箱</span
              ><input v-model.trim="form.email" class="manage-input" type="email" maxlength="128"
            /></label>
          </div>
          <p v-if="message" class="success-text">{{ message }}</p>
          <p v-if="error" class="manage-error">{{ error }}</p>
          <button class="manage-btn" :disabled="loading">
            {{ loading ? '保存中...' : '保存资料' }}
          </button>
        </form>
      </div>
    </section>
  </div>
</template>
<style scoped>
.profile-panel {
  max-width: 900px;
}
.profile-body {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 30px;
  padding: 28px;
}
.profile-body aside {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  border-right: 1px solid rgba(0, 242, 255, 0.12);
}
.large-avatar {
  width: 120px;
  height: 120px;
  border: 1px solid #00b8c7;
  display: grid;
  place-items: center;
  color: #00f2ff;
  font-size: 38px;
  overflow: hidden;
  background: rgba(0, 242, 255, 0.05);
  clip-path: polygon(
    16px 0,
    100% 0,
    100% calc(100% - 16px),
    calc(100% - 16px) 100%,
    0 100%,
    0 16px
  );
}
.large-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.upload-btn {
  font-size: 12px;
}
.profile-body aside small {
  color: #496d74;
  font-size: 9px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  margin-bottom: 18px;
}
.form-grid label {
  display: grid;
  gap: 7px;
}
.form-grid span {
  color: #70969d;
  font-size: 12px;
}
.success-text {
  color: #00d49d;
  font-size: 12px;
}
@media (max-width: 700px) {
  .profile-body {
    grid-template-columns: 1fr;
  }
  .profile-body aside {
    border-right: 0;
    border-bottom: 1px solid rgba(0, 242, 255, 0.12);
    padding-bottom: 20px;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
