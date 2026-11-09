<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

/**
 * 单实例锁定页：本窗口已被另一个前端窗口顶掉。
 * 这里不启动实例闸门（main.js 会跳过），点“在此窗口继续”整页重载目标地址，
 * 由目标页重新占位——另一个窗口随即被顶到本页，实现窗口间交接。
 */

const route = useRoute()
const otherActive = ref(true)
const checked = ref(false)
let timer = 0

const from = computed(() => {
  const raw = String(route.query.from || '/home')
  // 只允许站内绝对路径，挡掉 //evil.com 这类协议相对地址
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/locked')) return '/home'
  return raw
})

async function refreshStatus() {
  try {
    const res = await fetch('/python-api/app-instance/status')
    const data = await res.json()
    otherActive.value = Boolean(data.active)
  } catch {
    otherActive.value = false
  } finally {
    checked.value = true
  }
}

function resume() {
  window.location.replace(from.value)
}

onMounted(() => {
  refreshStatus()
  timer = setInterval(refreshStatus, 3000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <main class="lock-screen">
    <div class="lock-grid" aria-hidden="true"></div>
    <section class="lock-card">
      <p class="lock-kicker">SINGLE INSTANCE</p>
      <h1>已在另一个窗口打开</h1>
      <p class="lock-lead">
        为保证后端的实时语音、数字人与检测任务只对应一个页面，本窗口已自动停用。<br />
        继续使用前，请关闭另一个窗口，或直接点下方按钮把控制权接回本窗口。
      </p>

      <p class="lock-state" :class="{ free: checked && !otherActive }">
        <span class="dot"></span>
        <template v-if="!checked">正在确认另一个窗口的状态…</template>
        <template v-else-if="otherActive">另一个窗口正在使用中，接管后它会被停用</template>
        <template v-else>当前没有其他窗口在用，可以直接继续</template>
      </p>

      <button class="lock-btn" type="button" @click="resume">在此窗口继续</button>
      <p class="lock-hint">按钮会重新加载 {{ from }}，并让另一个窗口进入同样的停用状态</p>
    </section>
  </main>
</template>

<style scoped>
.lock-screen {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: radial-gradient(circle at 50% 30%, #07314a 0, #00131f 38%, #000810 78%);
  color: #d5f4f7;
  font-family: 'HarmonyOS Sans SC', 'Microsoft YaHei', system-ui, sans-serif;
}

.lock-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 242, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 242, 255, 0.045) 1px, transparent 1px);
  background-size: 38px 38px;
  mask-image: radial-gradient(circle at 50% 45%, #000 20%, transparent 72%);
}

.lock-card {
  position: relative;
  width: min(560px, calc(100vw - 48px));
  padding: 42px 44px;
  border: 1px solid rgba(0, 242, 255, 0.28);
  background: rgba(0, 17, 29, 0.9);
  box-shadow:
    0 0 0 1px rgba(0, 242, 255, 0.06),
    0 30px 80px rgba(0, 0, 0, 0.55),
    0 0 60px rgba(0, 242, 255, 0.08);
  backdrop-filter: blur(6px);
}

.lock-card::before,
.lock-card::after {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  border: 1px solid #00f2ff;
}
.lock-card::before {
  top: -1px;
  left: -1px;
  border-right: 0;
  border-bottom: 0;
}
.lock-card::after {
  right: -1px;
  bottom: -1px;
  border-left: 0;
  border-top: 0;
}

.lock-kicker {
  margin: 0 0 14px;
  font-size: 11px;
  letter-spacing: 0.42em;
  color: #00f2ff;
}

.lock-card h1 {
  margin: 0 0 16px;
  font-size: 28px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: #eafcff;
}

.lock-lead {
  margin: 0 0 22px;
  font-size: 14px;
  line-height: 1.9;
  color: #a8eaf0;
}

.lock-state {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 26px;
  font-size: 13px;
  color: #ffad4a;
}

.lock-state .dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  background: #ffad4a;
  box-shadow: 0 0 10px #ffad4a;
}

.lock-state.free {
  color: #57e6b7;
}

.lock-state.free .dot {
  background: #57e6b7;
  box-shadow: 0 0 10px #57e6b7;
}

.lock-btn {
  width: 100%;
  padding: 13px 18px;
  border: 1px solid #00f2ff;
  background: rgba(0, 242, 255, 0.12);
  color: #00f2ff;
  font-size: 15px;
  letter-spacing: 0.18em;
  cursor: pointer;
  transition:
    background 0.18s ease,
    box-shadow 0.18s ease;
}

.lock-btn:hover {
  background: rgba(0, 242, 255, 0.22);
  box-shadow: 0 0 24px rgba(0, 242, 255, 0.28);
}

.lock-hint {
  margin: 14px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #527984;
  word-break: break-all;
}
</style>
