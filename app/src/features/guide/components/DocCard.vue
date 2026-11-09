<script setup>
/**
 * 资料展示框：重要资料（检测结果/指标解释/方案要点）在页面上固定呈现，
 * 而不是埋在聊天流里被刷走。科技蓝玻璃风，带扫码式边框与入场动效。
 *
 * 数据由后端 /companion/chat 的 doc 字段驱动：
 *   { kind:'doc', title, points: [] }        资料卡
 *   { kind:'module', key, title, path, desc, points }  模块卡（可跳转）
 */
import { computed } from 'vue'

const props = defineProps({
  card: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const isDoc = computed(() => props.card?.kind === 'doc')
const title = computed(() => props.card?.title || '重要资料')
const points = computed(() => props.card?.points || [])
</script>

<template>
  <transition name="doc-pop">
    <section v-if="card" class="doc-card">
      <header class="doc-head">
        <span class="doc-badge">{{ isDoc ? '资料' : '推荐' }}</span>
        <h4>{{ title }}</h4>
        <button class="doc-close" title="收起" @click="emit('close')">×</button>
      </header>
      <p v-if="card.desc" class="doc-desc">{{ card.desc }}</p>
      <ul class="doc-points">
        <li v-for="(p, i) in points" :key="i">
          <i>{{ String(i + 1).padStart(2, '0') }}</i>
          <span>{{ p }}</span>
        </li>
      </ul>
      <footer class="doc-foot">
        <router-link v-if="!isDoc && card.path" :to="card.path" class="doc-link">
          去检测 →
        </router-link>
        <small v-else>由数字人整理 · 仅供参考</small>
      </footer>
      <span class="doc-corner tl"></span>
      <span class="doc-corner tr"></span>
      <span class="doc-corner bl"></span>
      <span class="doc-corner br"></span>
    </section>
  </transition>
</template>

<style scoped>
.doc-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px 10px;
  border: 1px solid rgba(96, 165, 250, 0.38);
  border-radius: 14px;
  /* 近实底：浮层下面有对话文字，透底会产生重影（评审 s1） */
  background:
    linear-gradient(165deg, rgba(16, 38, 70, 0.985), rgba(7, 18, 34, 0.99)),
    radial-gradient(120% 80% at 50% 0%, rgba(59, 130, 246, 0.14), transparent 70%);
  backdrop-filter: blur(8px);
  box-shadow: 0 14px 40px rgba(2, 8, 22, 0.55), inset 0 0 40px rgba(59, 130, 246, 0.06);
  overflow: hidden;
}
/* 顶部扫描线光效 */
.doc-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 2px;
  background: linear-gradient(90deg, transparent 0%, #60a5fa 35%, #bfdbfe 50%, #60a5fa 65%, transparent 100%);
  box-shadow: 0 0 10px rgba(96, 165, 250, 0.8);
  animation: doc-scan 4.5s linear infinite;
}
@keyframes doc-scan {
  0% { transform: translateX(-55%); opacity: 0.5; }
  15% { opacity: 1; }
  85% { opacity: 1; }
  100% { transform: translateX(55%); opacity: 0.5; }
}
.doc-head {
  display: flex;
  align-items: center;
  gap: 9px;
}
.doc-badge {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid rgba(147, 197, 253, 0.5);
  background: rgba(59, 130, 246, 0.22);
  color: #dbeafe;
  font-size: 10px;
  letter-spacing: 2px;
}
.doc-head h4 {
  flex: 1;
  margin: 0;
  color: #eaf4ff;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.doc-close {
  width: 20px;
  height: 20px;
  margin-right: 4px;
  border: 1px solid rgba(147, 197, 253, 0.25);
  border-radius: 6px;
  background: transparent;
  color: #93b8dd;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: 0.18s;
}
.doc-close:hover {
  border-color: rgba(147, 197, 253, 0.6);
  color: #eaf4ff;
}
.doc-desc {
  margin: 0;
  color: #8fb4d8;
  font-size: 11.5px;
  line-height: 1.6;
}
.doc-points {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.doc-points li {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  padding: 5px 10px;
  border-left: 2px solid rgba(96, 165, 250, 0.5);
  border-radius: 0 8px 8px 0;
  background: rgba(30, 64, 112, 0.32);
}
.doc-points i {
  color: #60a5fa;
  font-size: 10px;
  font-style: normal;
  letter-spacing: 1px;
  padding-top: 2px;
}
.doc-points span {
  color: #cfe4fa;
  font-size: 12px;
  line-height: 1.65;
}
.doc-foot {
  display: flex;
  justify-content: flex-end;
  align-items: center;
}
.doc-foot small {
  color: #6f94b8;
  font-size: 10.5px;
}
.doc-link {
  padding: 4px 16px;
  border: 1px solid rgba(147, 197, 253, 0.6);
  border-radius: 999px;
  background: linear-gradient(100deg, #1e6fff, #38bdf8);
  color: #fff;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-decoration: none;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.45);
  transition: 0.18s;
}
.doc-link:hover {
  box-shadow: 0 6px 20px rgba(56, 189, 248, 0.6);
  filter: brightness(1.1);
}
/* 四角装饰 */
.doc-corner {
  position: absolute;
  width: 8px;
  height: 8px;
  border: 1px solid rgba(147, 197, 253, 0.55);
  pointer-events: none;
}
.doc-corner.tl { top: 4px; left: 4px; border-right: none; border-bottom: none; }
/* 右上角让位给关闭按钮，不画 bracket（评审 s2） */
.doc-corner.bl { bottom: 4px; left: 4px; border-right: none; border-top: none; }
.doc-corner.br { bottom: 4px; right: 4px; border-left: none; border-top: none; }

/* 入场：从右侧滑入 + 轻微缩放 */
.doc-pop-enter-active { transition: all 0.22s cubic-bezier(0.22, 1, 0.36, 1); }
.doc-pop-leave-active { transition: all 0.2s ease-in; }
.doc-pop-enter-from { opacity: 0; transform: translateY(-8px) scale(0.98); }
.doc-pop-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
