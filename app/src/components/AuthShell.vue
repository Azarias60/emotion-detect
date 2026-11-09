<script setup>
defineProps({ title: String, subtitle: String })
</script>
<template>
  <div class="auth-screen">
    <div class="grid-bg"></div>
    <section class="auth-card">
      <div class="auth-brand">
        <span>CUG</span>
        <h1>多模态健康检测系统</h1>
      </div>
      <div class="auth-title">
        <h2>{{ title }}</h2>
        <p>{{ subtitle }}</p>
      </div>
      <slot />
    </section>
    <RouterLink class="back-detect" to="/detect/emotion">← 返回检测页面</RouterLink>
  </div>
</template>
<style>
.auth-screen {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: radial-gradient(circle at 50% 30%, #07314a 0, #00131f 38%, #000810 78%);
  color: #d5f4f7;
  font-family: 'Microsoft YaHei', sans-serif;
  position: relative;
  overflow: hidden;
}
.grid-bg {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 242, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 242, 255, 0.035) 1px, transparent 1px);
  background-size: 38px 38px;
  mask-image: linear-gradient(to bottom, transparent, #000 25%, #000 75%, transparent);
}
.auth-card {
  position: relative;
  width: min(430px, calc(100vw - 30px));
  padding: 34px 38px;
  border: 1px solid rgba(0, 242, 255, 0.35);
  background: rgba(0, 17, 29, 0.9);
  box-shadow:
    0 0 45px rgba(0, 144, 190, 0.13),
    inset 0 0 35px rgba(0, 242, 255, 0.025);
  clip-path: polygon(
    18px 0,
    100% 0,
    100% calc(100% - 18px),
    calc(100% - 18px) 100%,
    0 100%,
    0 18px
  );
}
.auth-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 42px;
  width: 100px;
  height: 2px;
  background: #00f2ff;
  box-shadow: 0 0 10px #00f2ff;
}
.auth-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 25px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.14);
}
.auth-brand span {
  width: 44px;
  height: 44px;
  border: 1px solid #00f2ff;
  display: grid;
  place-items: center;
  color: #00f2ff;
  font-weight: 800;
}
.auth-brand h1 {
  font-size: 16px;
  letter-spacing: 2px;
  color: #a8eaf0;
  margin: 0;
}
.auth-title {
  margin: 24px 0;
}
.auth-title h2 {
  font-size: 24px;
  color: #00f2ff;
  margin: 0 0 6px;
  letter-spacing: 4px;
}
.auth-title p {
  margin: 0;
  color: #527984;
  font-size: 11px;
  letter-spacing: 1px;
}
.auth-form {
  display: grid;
  gap: 16px;
}
.auth-field {
  display: grid;
  gap: 7px;
}
.auth-field label {
  font-size: 12px;
  color: #74a1aa;
}
.auth-field input,
.auth-field select {
  width: 100%;
  padding: 12px 13px;
  border: 1px solid rgba(0, 242, 255, 0.22);
  background: #00101b;
  color: #d7f6f8;
  outline: none;
}
.auth-field input:focus {
  border-color: #00f2ff;
  box-shadow: 0 0 9px rgba(0, 242, 255, 0.13);
}
.auth-submit {
  padding: 12px;
  border: 1px solid #00d9ea;
  background: rgba(0, 242, 255, 0.12);
  color: #00f2ff;
  letter-spacing: 4px;
  cursor: pointer;
}
.auth-submit:disabled {
  opacity: 0.5;
}
.auth-switch {
  text-align: center;
  color: #668b94;
  font-size: 12px;
  margin: 18px 0 0;
}
.auth-switch a {
  color: #ffad4a;
  text-decoration: none;
}
.back-detect {
  position: absolute;
  top: 25px;
  left: 28px;
  color: #648d95;
  text-decoration: none;
  font-size: 12px;
}
.form-error {
  color: #ff736a;
  font-size: 12px;
  margin: 0;
}
</style>
