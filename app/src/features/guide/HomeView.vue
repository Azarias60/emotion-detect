<script setup>
import { useRouter } from 'vue-router'
import { computed, onMounted, ref, watch } from 'vue'
import DigitalHumanAvatar from './components/DigitalHumanAvatar.vue'
import DocCard from './components/DocCard.vue'
import { feedDoc, pushDoc } from './useDocFeed'
import { useHomeGuide } from './useHomeGuide'
import { queryReports } from '@/api/report'

const router = useRouter()
const avatarRef = ref(null)
const hasSpoken = ref(false) // 本页会话内首次说话走 greet 链（打招呼），之后直达 talk

// ---------- 实时导览员（useHomeGuide：与情感数字人页代码分割，persona=guide 内置） ----------
// 画面用 3D 动画数字人（用户拍板：此页不要对口型真人、也不需要 wav2lip）；
// 语音走一条 WebSocket（麦克风上行 + PCM 下行本地播放），不建 WebRTC、
// 不占引擎渲染管线；说话时驱动动画数字人切说话段。
// 功能只有：语音对话、情绪检测、模块推荐、打断——没有跳舞（那是陪伴页的）。
const talk = useHomeGuide()
const micText = ref('')

const MODULE_META = {
  health: { title: '健康问诊', subtitle: '舌象 · 心率 · 综合分析', path: '/detect/health' },
  fatigue: { title: '疲劳检测', subtitle: '眨眼 · 哈欠 · 疲劳评估', path: '/detect/fatigue' },
  emotion: { title: '情绪识别', subtitle: '表情 · 语音 · 情绪状态', path: '/detect/emotion' },
}

// 引擎就绪后开放对话控制台；引擎加载中/不可达时只有动画数字人静立待机
const liveActive = computed(() => ['connecting', 'connected'].includes(talk.connection.value))

// 模块 → 路由（语音确认跳转/推荐卡共用；companion 供语音直跳"情感数字人"用）
const MODULE_PATH = {
  health: '/detect/health',
  fatigue: '/detect/fatigue',
  emotion: '/detect/emotion',
  companion: '/detect/companion',
}

// 模块推荐常驻卡（2026-10-23 用户拍板）：默认就是暖暖推荐的健康问诊，
// 对话里分诊算法触发的推荐只「更换」卡片内容，不弹不收
const DEFAULT_REC = {
  kind: 'module',
  key: 'health',
  reason: '第一次来？建议先做一次健康问诊，了解身体基础状态。',
}
const currentRec = ref({ ...DEFAULT_REC })
const recommendModule = computed(() => {
  const rec = currentRec.value
  if (!rec || rec.kind !== 'module') return null
  return { meta: MODULE_META[rec.key] || null, reason: rec.reason || '' }
})

const resetHint = ref('')
let resetHintTimer = null
// 引擎刚推来推荐时，状态行同步提示 4 秒（推荐卡常驻，不靠它弹出）
let recHintTimer = null
watch(
  () => talk.recommendation.value,
  (rec) => {
    if (!rec) return
    resetHint.value = `已推荐「${MODULE_META[rec.key]?.title || '检测'}」，见右侧资料栏`
    clearTimeout(recHintTimer)
    recHintTimer = setTimeout(() => {
      // 只清自己写的这条：期间若用户又触发别的提示（如刷新），保留最新的
      if (resetHint.value && resetHint.value.startsWith('已推荐')) resetHint.value = ''
    }, 4000)
  },
)

const statusText = computed(() => {
  if (resetHint.value) return resetHint.value
  if (talk.needPlayTap.value) return '点一下页面任意处，开启暖暖的语音'
  return talk.connectionDetail.value || '暖暖正在醒来，请稍等…'
})


const modules = [
  {
    key: 'health',
    title: '健康问诊',
    subtitle: '舌象 · 心率 · 综合分析',
    path: '/detect/health',
    image: new URL('./assets/health-module.png', import.meta.url).href,
    position: 'topleft',
  },
  {
    key: 'fatigue',
    title: '疲劳检测',
    subtitle: '眨眼 · 哈欠 · 疲劳评估',
    path: '/detect/fatigue',
    image: new URL('./assets/fatigue-module.png', import.meta.url).href,
    position: 'bottomleft',
  },
  {
    key: 'emotion',
    title: '情绪识别',
    subtitle: '表情 · 语音 · 情绪状态',
    path: '/detect/emotion',
    image: new URL('./assets/emotion-module.png', import.meta.url).href,
    position: 'topright',
  },
  {
    key: 'companion',
    title: '情感数字人',
    subtitle: '陪伴 · 对话 · 心理支持',
    path: '/detect/companion',
    image: new URL('./assets/companion-module.png', import.meta.url).href,
    position: 'bottomright',
  },
]

function go(path) {
  router.push(path)
}

// 首页资料栏：默认投放"如何获得准确结果"的使用指引；
// 任何页面（检测结果等）都可以通过 pushDoc 覆盖成新的重要资料。
const GUIDE = {
  title: '如何获得准确结果',
  desc: '开始检测前，请确认以下几点',
  points: [
    '光线充足，正脸对镜头，不遮挡',
    '检测前静坐 30 秒，保持自然表情',
    '语音请说普通话，环境保持安静',
    '疲劳与情绪检测约 1 分钟，期间不要切走页面',
    '健康问诊先拍舌象，再回答几道问诊题',
    '做完检测回到首页，暖暖会读一遍结论要点',
    '结果仅供健康参考，不能替代医生诊断',
  ],
}
onMounted(() => {
  if (!feedDoc.value) pushDoc(GUIDE)
  talk.init()
})

// 检测结果回读（用户拍板 2026-10-23）：登录用户做完检测回到首页，
// 暖暖语音讲一句关键结论 + 资料卡展示要点；报告读不到时静默跳过。
const reportRead = ref(false)
watch(
  () => talk.connection.value,
  async (state) => {
    if (state !== 'connected' || reportRead.value) return
    reportRead.value = true
    const token = localStorage.getItem('emotion-token')
    if (!token) return // 游客不回读
    let latest = null
    try {
      const res = await queryReports({ pageNum: 1, pageSize: 1 })
      const rows = res?.data?.rows || res?.data || []
      latest = rows[0] || null
    } catch {
      return
    }
    if (!latest || !latest.summary) return
    const points = latest.summary
      .split(/[;；。\n]/)
      .map((t) => t.trim())
      .filter((t) => t.length >= 4)
      .slice(0, 4)
    if (!points.length) return
    pushDoc({ title: `上次检测 · ${latest.title || '健康报告'}`, points })
    talk.speak(`欢迎回来。你上次做的${latest.title || '检测'}结果在这儿：${points.slice(0, 2).join('，')}。要看完整内容，可以看右边的资料卡。`)
  },
)

// LLM 回答要点自动进资料栏：预置指引被第一条真实回答覆盖（可关闭，再问再出）
watch(
  () => talk.docCard.value,
  (card) => {
    if (card && liveActive.value && card.title && card.points?.length) {
      pushDoc({ title: card.title, points: card.points })
    }
  },
)

// 对话触发的模块推荐 → 换常驻推荐卡内容 + 暖暖语音询问是否现在去测
watch(
  () => talk.recommendation.value,
  (rec) => {
    if (rec && rec.kind === 'module' && MODULE_META[rec.key]) {
      currentRec.value = { kind: 'module', key: rec.key, reason: rec.reason || '' }
    }
  },
)

// 语音确认：用户对推荐说「好/可以」→ 引擎判定后发 module_jump，直接跳转
watch(
  () => talk.moduleJump.value,
  (jump) => {
    if (!jump) return
    talk.clearance()
    const path = MODULE_PATH[jump.module]
    if (path) router.push(path)
  },
)

// 刷新口令/新用户接入：greet 重新待命 + 推荐卡回默认 + 资料卡回预置
watch(
  () => talk.sessionReset.value,
  (n) => {
    if (!n) return
    hasSpoken.value = false
    currentRec.value = { ...DEFAULT_REC }
    if (!feedDoc.value) pushDoc(GUIDE)
    // 重置文案优先显示 3 秒，之后自动让位给正常状态流
    resetHint.value = '会话已重新开始，说吧'
    clearTimeout(resetHintTimer)
    recHintTimer && clearTimeout(recHintTimer)
    resetHintTimer = setTimeout(() => {
      if (resetHint.value === '会话已重新开始，说吧') resetHint.value = ''
    }, 3000)
  },
)

// 说话动画与语音对齐（2026-10-24 用户拍板"张嘴和语音一起播放，结束时也别差太多"）：
// idle2talk/talk2talk/greet2talk 全是说话动画——起止都硬切对齐声音：
// - 起：语音真正起播（audio_start→speaking）那帧，首句硬切 greet、后续硬切 idle2talk
// - 止：本地播放排空（talking 置假）那帧，硬切回 idle 过渡，不留"张嘴没声音"尾巴
// 姿势跳变按 greetNow 同口径接受（对齐优先于无缝）。
watch(
  () => talk.speaking.value,
  (on) => {
    if (!on) return
    const avatar = avatarRef.value
    if (!avatar) return
    if (hasSpoken.value) {
      avatar.talkNow()
    } else {
      hasSpoken.value = true
      avatar.greetNow()
    }
  },
)
watch(
  () => talk.talking.value,
  (on) => {
    if (on) return // 起播由 speaking（audio_start）驱动，transcript 时不起链
    avatarRef.value?.stopTalkingNow()
  },
)

function sendMicText() {
  const text = micText.value.trim()
  if (!text) return
  if (talk.needPlayTap.value) talk.tapPlay()
  talk.sendText(text)
  micText.value = ''
}

function acceptRecommend() {
  const target = recommendModule.value?.meta
  if (!target) return
  talk.clearance()
  router.push(target.path)
}

</script>

<template>
  <div class="home-screen">
    <!-- 被静音横幅（2026-10-25）：多窗口同开时被仲裁掉的那个窗口必须一眼可见，
         否则用户在静音窗口里说话/等跳转，所有事件都发生在另一个窗口里 -->
    <div v-if="talk.micYielded.value" class="muted-banner">
      本窗口已静音——检测中心在另一个窗口打开了。请关闭其他窗口，然后
      <b @click="talk.retry()">刷新本页接管麦克风</b>
    </div>
    <header class="home-header">
      <div class="brand">
        <span class="brand-mark">CUG</span>
        <div>
          <h1>多模态健康检测</h1>
        </div>
      </div>
      <div class="home-header-right">
        <div class="system-state">
          <span></span>{{ recommendModule ? '已为你推荐 · 见右侧' : '聆听中' }}
        </div>
      </div>
    </header>

    <main class="home-main">
      <!-- 左栏·使用指引（2026-10-25）：原来挤在页头中间的三个步骤移到这里，
           人物居中后左半边只剩背景，用这张卡补上内容并与右侧资料栏形成对称 -->
      <aside class="guide-rail">
        <div class="guide-card">
          <div class="guide-head">怎么用</div>
          <div class="guide-step"><i>1</i><strong>说「你好」开始</strong></div>
          <div class="guide-step"><i>2</i><strong>按推荐去检测</strong></div>
          <div class="guide-step"><i>3</i><strong>做完回来听讲解</strong></div>
        </div>
        <!-- 语音识别面板（2026-10-25 从右栏移来）：左侧=使用指引+情绪+收听状态，
             右侧=推荐+回复+资料，两栏等高、语义分区清楚 -->
        <div class="asr-panel">
          <div class="asr-row-top">
            <span class="asr-state" :class="{ on: talk.listening.value }">{{ talk.listening.value ? '● 常听中' : '○ 未开麦' }}</span>
            <span class="asr-device" :title="talk.micLabel.value">{{ (talk.micLabel.value || '获取设备中…').split(' (')[0] }}</span>
          </div>
          <div class="asr-meter"><i :style="{ width: Math.round(talk.micLevel.value * 100) + '%' }"></i></div>
          <!-- 2026-10-25：唤醒前完全安静（不显示任何提示/拦截记录），
               听到「你好」后才开始记录 -->
          <div v-if="talk.awake.value" class="asr-log">
            <div v-for="(item, i) in [...talk.heardLog.value].reverse()" :key="i" class="asr-row" :class="'asr-' + item.kind">
              <span class="asr-kind">{{ item.kind }}</span>
              <span class="asr-text">{{ item.text }}</span>
            </div>
            <div v-if="!talk.heardLog.value.length" class="asr-empty">说句话试试，识别结果会显示在这里</div>
          </div>
        </div>
      </aside>
      <div class="home-stage">
      <div class="radar-grid"></div>
      <div class="orbit orbit-large"></div>
      <div class="orbit orbit-medium"></div>
      <div class="orbit orbit-small"></div>
      <div class="orbit-dash dash-a"></div>
      <div class="orbit-dash dash-b"></div>
      <div class="connection connection-top"></div>
      <div class="connection connection-right"></div>
      <div class="connection connection-left"></div>
      <div class="connection connection-bottom"></div>

      <button
        v-for="item in modules"
        :key="item.key"
        class="module-orb"
        :class="`module-${item.position}`"
        :data-module="item.key"
        @click="go(item.path)"
      >
        <!-- 2026-10-25 减法：去掉图标外的旋转环/扫描线/十字线（截图里那圈
             六边形杂线），只留四角取景框——更干净，图标本身才是主角 -->
        <span class="orb-image">
          <i class="orb-corner corner-tl"></i><i class="orb-corner corner-tr"></i
          ><i class="orb-corner corner-bl"></i><i class="orb-corner corner-br"></i>
          <img :src="item.image" :alt="item.title" />
        </span>
        <span class="orb-label"
          ><strong>{{ item.title }}</strong
          ><small>{{ item.subtitle }}</small></span
        >
      </button>

      <section class="human-core" aria-label="人体健康监测中心">
        <div class="core-glow"></div>
        <DigitalHumanAvatar ref="avatarRef" class="human-figure" mode="idle" size="sm" aria-label="动画数字人暖暖" />

        <div class="core-caption">
          <span class="pulse-dot"></span><strong>人体健康监测中心</strong>
        </div>

        <!-- 对话控制台：常驻（未连接时禁用），布局恒定不跳动——
             人物高度按"标题+控制台在场"预留，连接前后完全一致 -->
        <!-- 控制台（2026-10-25 合并为一行）：状态灯+文案、输入框、发送同一胶囊，
             省下的 40px 全给了人物——原来两行布局把人物压到 249px 宽 -->
        <div class="core-console" @click.capture="talk.needPlayTap.value && talk.tapPlay()">
          <span class="console-status">
            <span class="pulse-dot"></span>
            <span class="status-text">{{ statusText }}</span>
          </span>
          <input
            v-model="micText"
            class="ctl-input"
            placeholder="不方便说话时，也可以打字"
            :disabled="talk.connection.value !== 'connected'"
            @keyup.enter="sendMicText"
          />
          <button class="ctl-send" :disabled="talk.connection.value !== 'connected'" @click.stop="sendMicText">发送</button>
        </div>
      </section>
      </div>

      <!-- 右侧资料栏：常驻不收起（2026-10-23 用户拍板）——
           暖暖推荐卡常驻（对话推荐只换内容）+ 资料卡只被替换（不可关闭） -->
      <aside class="doc-rail">
        <div class="rail-body">
          <div v-if="recommendModule" class="rec-card">
            <div class="rec-head">暖暖推荐</div>
            <strong v-if="recommendModule.meta" class="rec-title">{{ recommendModule.meta.title }}</strong>
            <p class="rec-reason">{{ recommendModule.reason }}</p>
            <div class="rec-foot">
              <span class="rec-voice">说「好」直接前往</span>
              <button class="rec-go" @click="acceptRecommend">前往检测</button>
            </div>
          </div>
          <DocCard v-if="feedDoc" :card="feedDoc" />
          <p v-else class="rail-empty">暂无资料</p>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.home-header-right {
  display: flex;
  align-items: center;
  gap: 18px;
}
.system-state {
  display: flex;
  align-items: center;
  color: #699aa3;
  font-size: 11px;
  letter-spacing: 1px;
}
.system-state span {
  width: 7px;
  height: 7px;
  margin-right: 8px;
  border-radius: 50%;
  background: #00efbc;
  box-shadow: 0 0 9px #00efbc;
  animation: pulse 1.6s infinite;
}
.home-screen {
  min-height: 100vh;
  /* clip 与 hidden 的关键区别：clip 彻底禁止程序化滚动（scrollIntoView/
     focus 也不会再把这层顶上去）。此前规则末尾残留的 overflow:hidden 覆盖了
     clip，推荐卡/输入框聚焦时整页被顶上去 ~125px、页头消失（2026-10-23
     用户反馈"一说话内容上移"+ GLM 复验 high）。此规则内不许再写 overflow。 */
  overflow: clip;
  background:
    radial-gradient(circle at 50% 48%, rgba(0, 91, 130, 0.22), transparent 34%),
    linear-gradient(135deg, #000912, #00131f 55%, #000912);
  color: #ccecf0;
  font-family: 'Microsoft YaHei', Arial, sans-serif;
}
/* 被静音横幅：多窗口仲裁后必须一眼可见（2026-10-25） */
.muted-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 99;
  padding: 9px 16px;
  text-align: center;
  color: #ffd9a8;
  font-size: 13px;
  letter-spacing: 0.5px;
  background: linear-gradient(180deg, rgba(74, 44, 8, 0.97), rgba(46, 27, 5, 0.97));
  border-bottom: 1px solid rgba(255, 157, 46, 0.6);
  box-shadow: 0 4px 20px rgba(255, 157, 46, 0.25);
}
.muted-banner b {
  color: #ffb45c;
  text-decoration: underline;
  cursor: pointer;
  margin-left: 4px;
}

.home-header {
  position: relative;
  height: 70px;
  margin: 0 26px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.22);
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  z-index: 5;
}
.brand,
.home-actions,
.user-entry,
.user-copy {
  display: flex;
  align-items: center;
}
.brand {
  gap: 13px;
}
.brand-mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border: 1px solid #00f2ff;
  color: #00f2ff;
  font-size: 12px;
  font-weight: 800;
  box-shadow:
    inset 0 0 16px rgba(0, 242, 255, 0.2),
    0 0 10px rgba(0, 242, 255, 0.16);
  clip-path: polygon(
    10px 0,
    100% 0,
    100% calc(100% - 10px),
    calc(100% - 10px) 100%,
    0 100%,
    0 10px
  );
}
.brand h1 {
  margin: 0;
  color: #00f2ff;
  font-size: 21px;
  letter-spacing: 3px;
  text-shadow: 0 0 12px rgba(0, 242, 255, 0.35);
}
.brand p {
  margin: 4px 0 0;
  color: #5b8e99;
  font-size: 10px;
  letter-spacing: 2px;
}
.home-actions {
  gap: 22px;
}
.header-link,
.user-entry {
  color: #83cbd2;
  text-decoration: none;
  font-size: 12px;
  letter-spacing: 1px;
}
.header-link {
  padding: 9px 14px;
  border: 1px solid rgba(0, 242, 255, 0.28);
  background: rgba(0, 31, 48, 0.75);
  clip-path: polygon(7px 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%, 0 7px);
}
.header-link span {
  color: #ff9d2e;
  margin-right: 6px;
}
.header-link:hover {
  color: #00f2ff;
  border-color: #00f2ff;
}
.user-entry {
  gap: 9px;
}
.user-avatar {
  width: 37px;
  height: 37px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid rgba(0, 242, 255, 0.5);
  color: #00f2ff;
  background: #001b2b;
}
.user-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.user-copy {
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.user-copy strong {
  color: #b9e9ed;
  font-size: 12px;
}
.user-copy small {
  color: #56818a;
  font-size: 10px;
}
.home-main {
  height: calc(100vh - 70px);
  min-height: 600px;
  position: relative;
  display: grid;
  place-items: center;
}
.radar-grid {
  position: absolute;
  /* ≤596：720p 下底缘 699，不再触碰屏幕底边（曾 726 越界，用户反馈"圆弧碰到边界"） */
  width: min(596px, 66vw);
  height: min(596px, 66vw);
  border: 1px solid rgba(0, 242, 255, 0.13);
  background-image:
    linear-gradient(rgba(0, 242, 255, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 242, 255, 0.08) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: radial-gradient(circle, #000 42%, transparent 73%);
  opacity: 0.65;
}
.orbit {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(0, 242, 255, 0.22);
  box-shadow: 0 0 28px rgba(0, 242, 255, 0.06);
}
.orbit-large {
  width: min(596px, 66vw);
  height: min(596px, 66vw);
  border-color: rgba(0, 242, 255, 0.2);
  animation: spin 30s linear infinite;
}
.orbit-medium {
  width: min(495px, 55vw);
  height: min(495px, 55vw);
  border-color: rgba(255, 157, 46, 0.25);
  border-style: dashed;
  animation: spin-reverse 22s linear infinite;
}
.orbit-small {
  width: min(375px, 42vw);
  height: min(375px, 42vw);
  border-color: rgba(0, 242, 255, 0.34);
}
.orbit-dash {
  position: absolute;
  border: 2px solid #00f2ff;
  border-left-color: transparent;
  border-right-color: transparent;
  border-radius: 50%;
  opacity: 0.8;
}
.dash-a {
  width: min(596px, 66vw);
  height: min(596px, 66vw);
  transform: rotate(24deg);
  border-top-color: transparent;
}
.dash-b {
  width: min(530px, 59vw);
  height: min(530px, 59vw);
  transform: rotate(-32deg);
  border-bottom-color: #ff9d2e;
  border-top-color: transparent;
}
.connection {
  position: absolute;
  background: #00f2ff;
  opacity: 0.5;
  box-shadow: 0 0 8px #00f2ff;
}
/* 装饰连线（2026-10-25）：旧十字布局用来把上/下模块接到人体，左右夹持后
   四模块与人体之间的间隙只有 20px，长连线会戳进模块图标——全部隐去，
   改由模块自身的辉光与轨道环表达连接关系。 */
.connection {
  display: none;
}
.module-orb {
  position: absolute;
  width: 165px;
  background: transparent;
  border: 0;
  color: #ccecf0;
  cursor: pointer;
  padding: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 8px;
}
.orb-image {
  width: 128px;
  height: 128px;
  border-radius: 50%;
  padding: 7px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(0, 242, 255, 0.52);
  background: rgba(0, 24, 40, 0.94);
  box-shadow:
    0 0 0 7px rgba(0, 242, 255, 0.05),
    0 0 24px rgba(0, 242, 255, 0.22);
  transition: 0.25s;
}
.orb-image img {
  width: 100%;
  height: 100%;
  display: block;
}
.orb-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: center;
  text-shadow: 0 0 6px rgba(0, 242, 255, 0.16);
  position: relative;
  padding: 6px 14px 4px;
}
/* 暗色软底衬：背景装饰轨道线原本直接穿过模块标题（评审 medium），
   垫一层径向暗晕后线条在文字附近淡出，不影响标题可读性 */
.orb-label::before {
  content: '';
  position: absolute;
  inset: -2px -6px;
  z-index: -1;
  border-radius: 14px;
  background: radial-gradient(ellipse at center, rgba(0, 9, 17, 0.92) 30%, rgba(0, 9, 17, 0.55) 62%, transparent 78%);
  pointer-events: none;
}
.orb-label strong {
  font-size: 14px;
  color: #00f2ff;
  letter-spacing: 2px;
}
.orb-label small {
  font-size: 11px;
  color: #a5ced6;
  white-space: nowrap;
}
.module-orb:hover .orb-image {
  transform: scale(1.08);
  border-color: #ff9d2e;
  box-shadow:
    0 0 0 8px rgba(255, 157, 46, 0.08),
    0 0 32px rgba(255, 157, 46, 0.35);
}
.module-orb:hover .orb-label strong {
  color: #ffb45c;
}
.human-core {
  width: 240px;
  /* 2026-10-25：改 auto——人物会长到 520px 高，硬编码 330 会让整列溢出到页头 */
  height: auto;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
/* 注意：不设 z-index——position:relative + z-index:auto 不形成层叠上下文，
   数字人视频的 mix-blend-mode:screen 才能一路混合到页面深色背景，
   黑底视频与背景无缝融合（组件内部控制混合与投影）。 */
.core-glow {
  position: absolute;
  width: 220px;
  height: 270px;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(0, 166, 220, 0.24), transparent 68%);
  filter: blur(7px);
}
.core-caption {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  padding: 4px 18px;
  /* 人物与标题行留 2px 呼吸位（副标题行已去除，单行更紧凑） */
  margin-top: 2px;
}
/* 中央标题同模块标题：装饰弧线会横穿文字行，垫一层径向暗晕让线在字附近淡出 */
.core-caption::before {
  content: '';
  position: absolute;
  inset: -4px -10px;
  z-index: -1;
  border-radius: 16px;
  background: radial-gradient(ellipse at center, rgba(0, 9, 17, 0.9) 34%, rgba(0, 9, 17, 0.5) 66%, transparent 80%);
  pointer-events: none;
}
.core-caption strong {
  color: #d4f8fa;
  font-size: 13px;
  letter-spacing: 2px;
}
.core-caption small {
  color: #7ea9b2;
  font-size: 11px;
  letter-spacing: 1px;
  white-space: nowrap;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #00efbc;
  box-shadow: 0 0 9px #00efbc;
  animation: pulse 1.6s infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes spin-reverse {
  to {
    transform: rotate(-360deg);
  }
}
@keyframes pulse {
  50% {
    opacity: 0.35;
    transform: scale(0.7);
  }
}
@media (max-width: 900px) {
  .home-header {
    margin: 0 14px;
    height: auto;
    padding: 14px 0;
    align-items: flex-start;
    gap: 12px;
    flex-direction: column;
  }
  .home-actions {
    width: 100%;
    justify-content: space-between;
  }
  .home-main {
    height: auto;
    min-height: 850px;
    transform: scale(0.86);
  }
  .module-topright,
  .module-bottomright {
    left: calc(50% + 14px + var(--human-half));
  }
  .module-topleft,
  .module-bottomleft {
    right: calc(50% + 14px + var(--human-half));
  }
}
@media (max-width: 620px) {
  .home-main {
    transform: scale(0.68);
    min-height: 760px;
    margin-top: -40px;
  }
  .home-actions {
    gap: 8px;
  }
  .header-link {
    padding: 8px;
  }
  .brand h1 {
    font-size: 17px;
  }
  .brand p {
    font-size: 8px;
  }
}
/* 数字人视频：704:1280 比例。高度按"标题行+对话控制台常驻"预留
   （calc(100vh-250px)），连接前后尺寸一致——曾因连接后收矮 80px 导致
   整页内容上移、比例突变（2026-10-23 用户反馈）。
   1080p 下仍为原设计 636 高。 */
.human-figure {
  /* 2026-10-25：控制台并成一行后人物可以放大一档（720p 452→520 高）
     min(636px, ...) 保证 1080p 仍是原设计高度 */
  width: min(350px, calc((100vh - 200px) * 704 / 1280));
  height: min(636px, calc(100vh - 200px));
  position: relative;
  flex: none;
}

/* 科技风模块按钮：与中央人体的青蓝发光线框保持统一 */
.module-orb {
  width: 194px;
  /* 16px：按钮图片底边有外扩辉光，10px 时间距不够、中文标题上沿会与辉光交叠吃字 */
  gap: 16px;
  transition: filter 0.35s ease;
}
.orb-image {
  width: 150px;
  height: 150px;
  position: relative;
  display: grid;
  place-items: center;
  border-radius: 50%;
  padding: 10px;
  overflow: visible;
  background: radial-gradient(circle, rgba(0, 175, 230, 0.2), rgba(0, 18, 35, 0.96) 62%);
  border: 1px solid rgba(0, 242, 255, 0.8);
  box-shadow:
    0 0 0 5px rgba(0, 242, 255, 0.05),
    0 0 0 10px rgba(0, 242, 255, 0.025),
    0 0 28px rgba(0, 210, 255, 0.4),
    inset 0 0 25px rgba(0, 180, 230, 0.24);
  transition:
    transform 0.35s ease,
    border-color 0.35s ease,
    box-shadow 0.35s ease;
}
.orb-image::before {
  content: '';
  position: absolute;
  inset: -9px;
  border: 1px dashed rgba(0, 242, 255, 0.8);
  border-radius: 50%;
  border-top-color: #ff9d2e;
  border-bottom-color: transparent;
  animation: orb-spin 8s linear infinite;
  pointer-events: none;
}
.orb-image::after {
  content: '';
  position: absolute;
  inset: -17px;
  border-radius: 50%;
  border: 1px solid rgba(0, 242, 255, 0.28);
  border-left-color: transparent;
  border-right-color: #ff9d2e;
  animation: orb-spin-reverse 12s linear infinite;
  pointer-events: none;
}
.orb-image img {
  width: 100%;
  height: 100%;
  display: block;
  position: relative;
  z-index: 2;
  filter: drop-shadow(0 0 7px rgba(0, 242, 255, 0.5));
}
.orb-ring {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  z-index: 1;
}
.ring-one {
  inset: 20px;
  border: 1px solid rgba(0, 242, 255, 0.38);
  border-left-color: transparent;
  animation: orb-spin 5s linear infinite;
}
.ring-two {
  inset: 7px;
  border: 1px dotted rgba(255, 157, 46, 0.45);
  border-top-color: transparent;
  animation: orb-spin-reverse 6s linear infinite;
}
.orb-scan {
  position: absolute;
  left: 15px;
  right: 15px;
  top: 50%;
  height: 1px;
  z-index: 3;
  background: #00f2ff;
  box-shadow: 0 0 10px #00f2ff;
  opacity: 0.65;
  animation: orb-scan-line 2.5s ease-in-out infinite;
}
.orb-cross {
  position: absolute;
  z-index: 4;
  background: rgba(0, 242, 255, 0.38);
  pointer-events: none;
}
.cross-h {
  left: -14px;
  right: -14px;
  top: 50%;
  height: 1px;
}
.cross-v {
  top: -14px;
  bottom: -14px;
  left: 50%;
  width: 1px;
}
.orb-corner {
  position: absolute;
  width: 18px;
  height: 18px;
  z-index: 5;
  border-color: #00f2ff;
  pointer-events: none;
}
.corner-tl {
  left: -4px;
  top: -4px;
  border-left: 2px solid;
  border-top: 2px solid;
}
.corner-tr {
  right: -4px;
  top: -4px;
  border-right: 2px solid;
  border-top: 2px solid;
}
.corner-bl {
  left: -4px;
  bottom: -4px;
  border-left: 2px solid;
  border-bottom: 2px solid;
}
.corner-br {
  right: -4px;
  bottom: -4px;
  border-right: 2px solid;
  border-bottom: 2px solid;
}
.orb-label em {
  color: #7fd8e8;
  font-style: normal;
  font-size: 10.5px;
  letter-spacing: 1.5px;
}
.orb-label em b {
  color: #ff9d2e;
  font-size: 13px;
}
.module-orb:hover {
  filter: drop-shadow(0 0 18px rgba(0, 242, 255, 0.45));
}
.module-orb:hover .orb-image {
  transform: scale(1.08);
  border-color: #ff9d2e;
  box-shadow:
    0 0 0 8px rgba(255, 157, 46, 0.08),
    0 0 32px rgba(255, 157, 46, 0.35);
}
.module-orb:hover .orb-label strong {
  color: #ffb45c;
}
@keyframes orb-spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes orb-spin-reverse {
  to {
    transform: rotate(-360deg);
  }
}
@keyframes orb-scan-line {
  0%,
  100% {
    transform: translateY(-42px);
    opacity: 0.15;
  }
  50% {
    transform: translateY(42px);
    opacity: 0.9;
  }
}

/* 参考玻璃舱体卡片比例 */
.orb-image {
  width: 160px;
  height: 160px;
  border-radius: 30px;
  padding: 7px;
  background: linear-gradient(145deg, rgba(7, 65, 100, 0.9), rgba(0, 19, 33, 0.98));
  box-shadow:
    0 0 0 4px rgba(0, 242, 255, 0.06),
    0 0 0 9px rgba(0, 242, 255, 0.025),
    0 0 30px rgba(0, 210, 255, 0.42),
    inset 0 0 28px rgba(0, 180, 230, 0.3);
}
.orb-image::before {
  /* 2026-10-25 减法：旧规则带 orb-spin 8s 动画 + 虚线边框，虽然被下面的
     实线边框覆盖过，animation 没被重置 —— 图标外一直有一圈"转动的虚线环"，
     正是用户嫌丑的六边形杂线。这里显式清掉动画与虚线段。 */
  inset: -7px;
  border-radius: 32px;
  border: 1px solid rgba(0, 242, 255, 0.55);
  border-top-color: #d7ffff;
  border-right-color: #ff9d2e;
  animation: none;
}
.orb-image::after {
  inset: -11px;
  border-radius: 36px;
  border: 1px solid rgba(0, 242, 255, 0.22);
  border-left-color: #8dfaff;
  border-bottom-color: #ff9d2e;
  animation: none;
}
.orb-image img {
  border-radius: 24px;
  filter: drop-shadow(0 0 8px rgba(0, 242, 255, 0.58));
}
.ring-one {
  inset: 18px;
  border-radius: 28px;
}
.ring-two {
  inset: 5px;
  border-radius: 34px;
}
.module-orb:hover .orb-image {
  box-shadow:
    0 0 0 5px rgba(0, 242, 255, 0.12),
    0 0 0 10px rgba(255, 157, 46, 0.07),
    0 0 38px rgba(255, 157, 46, 0.5),
    inset 0 0 30px rgba(0, 190, 240, 0.34);
}

/* ════════ 四模块左右夹持（2026-10-25 用户拍板：数字人必须在正中间）════════
   旧布局是「上/右/左/下」四个方向散开 + 舞台右侧留 360px 给资料栏，
   结果人物恒偏左约 171px。现在：舞台占满全宽（padding-right 归零）→
   人物在屏幕正中心；四模块改为**左二右二竖直夹持**，资料栏窄化成悬浮层。
   模块内缘跟着人体实际半宽自适应（--human-half 与 .human-figure 同一公式），
   720p / 1080p 都不会压到人物，也不会顶到右侧资料栏。 */
.module-topleft,
.module-bottomleft {
  left: auto;
  right: calc(50% + 24px + var(--human-half));
  transform: none;
}
.module-topright,
.module-bottomright {
  right: auto;
  left: calc(50% + 24px + var(--human-half));
  transform: none;
}
.module-topleft,
.module-topright {
  /* 2026-10-25：模块列中心对齐**人物身体中心**（不是整列中心）——
     整列还含下方标题与控制台，居中会把模块压低下沉 42px，看着松散。
     两卡各 226 高、盒距 30，整列 482，中心上移 43 → -284。 */
  top: calc(50% - 284px);
}
.module-bottomleft,
.module-bottomright {
  top: calc(50% - 28px);
}

/* ════════ 首页布局重排 + 资料栏（2026-10-23）════════
   原布局问题：四模块与中央人物固定偏移散在页面中心，宽屏两侧大片空白；
   右侧新增资料栏后需为它留出独立通道，避免覆盖右侧模块。 */
.home-main {
  /* 2026-10-25：不再给右侧资料栏预留通道——人物要正居中，
     资料栏改成悬浮层压在右侧空白区上。
     --human-half = .human-figure 宽度的 1/2（人物宽 = min(350px, (100vh-268px)*704/1280)），
     模块内缘用它定位，任何屏幕高度都不会压到人物。 */
  --human-half: min(175px, (100vh - 200px) * 0.275);
  padding-right: 0;
}
/* 舞台：承接四模块的绝对定位，整体在剩余空间内居中 */
.home-stage {
  position: relative;
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
}
/* 资料栏：右侧独立通道，纵向居中 */
.doc-rail {
  position: absolute;
  /* 与左栏、模块列同顶边同高度：右上「暖暖推荐」、右下「资料卡」 */
  top: calc(50% - 284px);
  right: 12px;
  height: 482px;
  /* 人物与模块放大后右栏收到 252：模块外缘与资料栏留 15px 间隙 */
  width: 252px;
  max-height: calc(100% - 40px);
  z-index: 6;
}
.rail-body {
  flex: 1;
  min-width: 0;
  /* 2026-10-25：推荐卡保持自然高度，资料卡拉长吃掉剩余高度 */
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  /* 用户拍板 2026-10-24：不要滚轮。三卡总高已压进 720p 的 595px 内
     （识别框 202 + 推荐卡 160 + 资料卡 208 + 间距），正常不出滚动条；
     万一内容超长也只静默滚动，不显示滚动条 */
  scrollbar-width: none;
}
.rail-body::-webkit-scrollbar {
  display: none;
}
/* 左栏指引卡同样在窄屏隐藏（与右栏资料栏同策略） */
@media (max-width: 1180px) {
  .guide-rail {
    display: none;
  }
}
.rail-empty {
  margin: 0;
  padding: 14px;
  border: 1px dashed rgba(96, 165, 250, 0.25);
  border-radius: 12px;
  color: #6f94b8;
  font-size: 12px;
  text-align: center;
}
/* 宽高比偏窄（如 1280×1080）：人物放大到 350 宽会把右模块顶进资料栏，
   此处整体收一档（308 宽 = 560 高，维持 704:1280），半宽变量同步。 */
@media (max-width: 1500px) and (min-height: 820px) {
  .home-main {
    --human-half: 154px;
  }
  .human-figure {
    width: min(308px, calc((100vh - 268px) * 704 / 1280));
    height: min(560px, calc(100vh - 268px));
  }
}
/* 窄屏：模块通道容不下资料栏（左右各需要一个模块位），资料栏隐去，
   主舞台靠下方的 900px/620px 缩放兜底 */
@media (max-width: 1180px) {
  .doc-rail {
    display: none;
  }
}

/* 人物舞台（首页）：原组件是"全息舱体"——纯黑直角面板 + 2px 亮青描边，
   放在深蓝渐变背景上就是一块硬贴上去的黑图。
   解决：让视频的黑底直接透明（mix-blend-mode: screen，黑=对背景零影响），
   人物像投影悬在背景上，黑面板与描边一并消失。
   注意：**不能再给舞台加 mask/滤镜**——遮罩会创建层叠上下文，把 screen 混合
   隔离在舞台内部（实测羽化+screen 同开时四角仍是黑的，黑色没有透出去）。 */
.human-figure :deep(.dh-stage) {
  border-radius: 30px;
  background: transparent;
}
/* 顶部投光带在首页会形成一块硬边亮矩形（压在头顶上方），去掉 */
.human-figure :deep(.dh-stage::before) {
  display: none;
}
/* 去掉舱体描边与内阴影：黑底既已透明，再描边只会勾出一个"隐形盒子" */
.human-figure :deep(.dh-stage::after) {
  display: none;
}
/* 黑底透明：纯黑像素对背景零影响，暗色衣料会略透出背景（全息感） */
.human-figure :deep(.dh-video) {
  mix-blend-mode: screen;
}

/* ════════ 实时导览（2026-10-23，2026-10-24 改为 WS 直连）════════
   画面是 3D 动画数字人（用户拍板：此页不要对口型真人，也不需要 wav2lip）；
   语音走 /ws/conversation 一条 WebSocket：麦克风 PCM 上行、回复 PCM 下行
   由 useHomeGuide 在 AudioContext 里本地播放（无 WebRTC 隐藏 video）。 */

/* 对话控制台（2026-10-25 合并为一行胶囊）：状态灯+文案 | 输入框 | 发送。
   原来"状态行 32 + 操作条 47 + 间距"共 87px，合并后 50px——省下的高度
   全部让给人物（720p 下人物从 249 宽长到 286 宽）。
   宽度显式给足：父级 .human-core 只有 240px，不写宽度会被挤成换行。 */
.core-console {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  width: min(560px, 92vw);
  box-sizing: border-box;
  padding: 7px 8px 7px 15px;
  border: 1px solid rgba(0, 242, 255, 0.35);
  border-radius: 999px;
  background: rgba(0, 22, 38, 0.86);
  backdrop-filter: blur(10px);
  box-shadow: 0 0 20px rgba(0, 242, 255, 0.14);
}
.console-status {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 46%;
  padding-right: 10px;
  border-right: 1px solid rgba(0, 242, 255, 0.16);
}
.status-text {
  color: #8fb9c1;
  font-size: 11.5px;
  letter-spacing: 0.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ctl-input {
  flex: 1;
  min-width: 0;
  background: rgba(0, 31, 48, 0.45);
  border: 1px solid rgba(0, 242, 255, 0.18);
  border-radius: 999px;
  padding: 6px 14px;
  color: #a9ccd4;
  font-size: 11px;
  outline: none;
  transition: 0.2s;
}
.ctl-input::placeholder {
  color: #4f7b84;
}
.ctl-input:focus {
  border-color: rgba(0, 242, 255, 0.7);
  box-shadow: 0 0 12px rgba(0, 242, 255, 0.2);
}
.ctl-input:disabled {
  opacity: 0.45;
}
.ctl-send {
  flex: none;
  padding: 6px 12px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  border-radius: 999px;
  background: transparent;
  color: #7db8c4;
  font-size: 11px;
  letter-spacing: 2px;
  cursor: pointer;
  transition: 0.2s;
}
.ctl-send:hover:not(:disabled) {
  box-shadow: 0 0 20px rgba(0, 210, 255, 0.55);
}
.ctl-send:disabled {
  opacity: 0.4;
  cursor: default;
}
/* 资料栏内 DocCard 的局部微调（只影响首页，共用组件样式不动）：
   「资料」胶囊原本正好压住卡片左上角的方形装饰角标（评审 low），
   给卡头留一点上/左余量即可分开。 */
.doc-rail :deep(.doc-head) {
  padding-left: 3px;
  margin-top: 2px;
}

/* 资料卡首页放大版（用户拍板 2026-10-23：资料页可以大一些）：
   内边距/字号/行距全面提升，要点区更透气，最多容纳 6 条要点 */
.doc-rail :deep(.doc-card) {
  /* 右栏只有 252px：内边距压到 11/12，让每行尽量放下完整一句 */
  padding: 12px 12px 12px;
  gap: 8px;
  border-radius: 14px;
  /* 2026-10-25：资料卡拉长铺满右栏剩余高度，要点在卡内均匀分布 */
  flex: 1;
  min-height: 0;
}
.doc-rail :deep(.doc-head) {
  margin-bottom: 2px;
}
.doc-rail :deep(.doc-badge) {
  padding: 2px 10px;
  font-size: 11px;
}
.doc-rail :deep(.doc-head h4) {
  font-size: 14px;
  letter-spacing: 0.5px;
}
.doc-rail :deep(.doc-points) {
  flex: 1;
  justify-content: space-between;
  gap: 5px;
}
/* 每条要点原来是一块带蓝底的小方块（上下 5px 内边距 + 底色 + 圆角），
   4 条叠起来显得一格一格很松散；改成细线清单，信息密度立刻上来 */
.doc-rail :deep(.doc-points li) {
  padding: 3px 0 3px 7px;
  background: none;
  border-left: 1px solid rgba(96, 165, 250, 0.45);
  border-radius: 0;
}
.doc-rail :deep(.doc-points li i) {
  padding-top: 1px;
}
.doc-rail :deep(.doc-points li span) {
  font-size: 12px;
  line-height: 1.5;
}
.doc-rail :deep(.doc-points li i) {
  font-size: 11px;
}

/* 资料卡只被替换不可关闭（用户拍板）：隐藏右上角 ×，陪伴页不受影响 */
.doc-rail :deep(.doc-close) {
  display: none;
}
/* 首页资料卡只留「标题+要点」：描述行与页脚属冗余文字（用户拍板去掉），
   免责声明移到右栏引导卡底部，信息不丢 */
.doc-rail :deep(.doc-desc),
.doc-rail :deep(.doc-foot) {
  display: none;
}

/* ════════ 左栏·使用指引（2026-10-25）════════
   人物居中后左半边原本只剩背景；把页头那三步操作说明收进这张竖卡：
   左侧指引 / 中央人物 / 右侧资料 三栏对称，信息也更集中。 */
.guide-rail {
  position: absolute;
  left: 12px;
  /* 顶边 = 模块列顶边、高度 = 模块列高（482）：两卡两端分开 →
     左上「怎么用」、左下「语音识别」，四角不再空着（用户："4 个角空闲太多"） */
  top: calc(50% - 284px);
  width: 250px;
  height: 482px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 6;
}
/* 2026-10-25：不再往中段塞新卡——把「语音识别」拉长吃掉剩余高度，
   卡内日志区跟着长高（能多显示两行识别记录），画面同样填满 */
.guide-rail > .asr-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  /* 状态行+音量条贴顶、识别记录贴底（下面日志用 margin-top:auto 推开）：
     三段均匀分布会让音量条漂到卡片正中，看着像悬空 */
  justify-content: flex-start;
}
.guide-card {
  padding: 12px 12px 11px;
  border: 1px solid rgba(0, 242, 255, 0.22);
  border-radius: 14px;
  background: linear-gradient(165deg, rgba(4, 22, 38, 0.86), rgba(2, 12, 22, 0.92));
  box-shadow: 0 10px 30px rgba(2, 8, 22, 0.4);
}
.guide-head {
  color: #8dfaff;
  font-size: 10.5px;
  letter-spacing: 2.5px;
  margin-bottom: 5px;
}
.guide-step {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 7px 0;
  border-top: 1px solid rgba(0, 242, 255, 0.1);
}
.guide-step:first-of-type {
  border-top: 0;
  padding-top: 0;
}
.guide-step i {
  flex: none;
  width: 17px;
  height: 17px;
  margin-top: 1px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(0, 242, 255, 0.45);
  background: rgba(0, 242, 255, 0.12);
  color: #8dfaff;
  font-size: 9px;
  font-style: normal;
}
.guide-step strong {
  display: block;
  color: #c8ecf2;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.guide-step em {
  display: block;
  margin-top: 2px;
  color: #6f97a3;
  font-size: 10.5px;
  font-style: normal;
  line-height: 1.45;
}

/* 语音识别面板（右栏顶部）：设备/音量/记录 */
.asr-panel {
  flex: none;
  padding: 12px 11px 12px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  border-radius: 14px;
  background: linear-gradient(165deg, rgba(8, 30, 52, 0.92), rgba(3, 14, 27, 0.95));
  box-shadow: 0 10px 30px rgba(2, 8, 22, 0.45), inset 0 0 30px rgba(0, 180, 230, 0.05);
  margin-bottom: 12px;
}
/* 状态灯与设备名同行：设备名去掉括号里的 ID 段并省略号截断，
   不再换行成两截（2026-10-25 用户嫌挤） */
.asr-row-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 5px;
}
.asr-device {
  flex: 1;
  min-width: 0;
  text-align: right;
  color: #6f9aa8;
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.asr-meter {
  height: 6px;
  border-radius: 3px;
  background: rgba(0, 31, 48, 0.8);
  border: 1px solid rgba(0, 242, 255, 0.18);
  overflow: hidden;
  margin-bottom: 6px;
}
.asr-meter i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #00c8ff, #7dfab0);
  box-shadow: 0 0 8px rgba(0, 220, 255, 0.7);
  transition: width 0.12s linear;
}
.asr-state {
  flex: none;
  color: #5f8a99;
  font-size: 10.5px;
  letter-spacing: 0.5px;
}
.asr-state.on {
  color: #73f8d0;
  text-shadow: 0 0 8px rgba(0, 239, 188, 0.4);
}
/* 识别日志：**恢复自适应高度**（曾改成固定 180px，空态也占 180px——用户
   "框框变大"即此）。空态只有一行提示，条目最多长到 92px（约 4 条，最新在
   上）——这个上限是按 720p 右栏总预算倒推的：128 面板 + 92 日志增量 +
   推荐卡 + 资料卡 ≤ 595，保证日志爆满也不出滚动条；再老的静默滚动。 */
.asr-log {
  /* 卡片拉长后把识别记录贴到卡底（最多 4 条）：状态行在上、记录在下，
     中间留白成为卡内的呼吸区，整卡不再显得空 */
  /* 记录区吃掉卡片下半部分：顶部一条分隔线把它划成独立区块。
     2026-10-25：不限条数 + 显示细滚轮（用户要求能滚看上下文） */
  flex: 1;
  min-height: 44px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 242, 255, 0.12);
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 242, 255, 0.35) transparent;
  overflow-y: auto;
  scrollbar-width: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.asr-log::-webkit-scrollbar {
  width: 4px;
}
.asr-log::-webkit-scrollbar-thumb {
  background: rgba(0, 242, 255, 0.3);
  border-radius: 2px;
}
.asr-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  line-height: 1.45;
}
.asr-time {
  flex: none;
  color: #47707e;
  font-size: 9.5px;
}
.asr-kind {
  flex: none;
  padding: 0 5px;
  border-radius: 6px;
  font-size: 9px;
  border: 1px solid;
}
.asr-识别 .asr-kind { color: #7dfab0; border-color: rgba(0, 239, 188, 0.4); }
.asr-未唤醒 .asr-kind { color: #ffc38d; border-color: rgba(255, 157, 46, 0.4); }
.asr-提示 .asr-kind { color: #9db9e8; border-color: rgba(120, 150, 220, 0.4); }
.asr-系统 .asr-kind { color: #8dfaff; border-color: rgba(0, 242, 255, 0.4); }
.asr-text {
  color: #c8e6ea;
  word-break: break-all;
}
.asr-empty {
  color: #47707e;
  font-size: 10.5px;
  line-height: 1.4;
  padding-top: 2px;
}

/* 模块推荐卡（资料栏顶部，连续负向情绪触发） */
.rec-card {
  flex: none;
  padding: 12px 12px 11px;
  margin-bottom: 12px;
  border: 1px solid rgba(255, 157, 46, 0.55);
  border-radius: 14px;
  background: linear-gradient(160deg, rgba(52, 32, 8, 0.92), rgba(22, 14, 5, 0.96));
  box-shadow: 0 0 22px rgba(255, 157, 46, 0.18);
  margin-bottom: 11px;
}
.rec-head {
  color: #ffb45c;
  font-size: 10.5px;
  letter-spacing: 3px;
  margin-bottom: 5px;
}
.rec-title {
  display: block;
  color: #ffd9a8;
  font-size: 14px;
  letter-spacing: 1.5px;
}
.rec-sub {
  display: block;
  color: #9a8367;
  font-size: 11px;
  letter-spacing: 1px;
  margin-top: 3px;
}
.rec-reason {
  margin: 5px 0 7px;
  color: #c7b49a;
  font-size: 11.5px;
  line-height: 1.5;
}
.rec-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 1px;
}
.rec-voice {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: #d8ac74;
  font-size: 10.5px;
  letter-spacing: 0.3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rec-go {
  flex: none;
  padding: 4px 11px;
  border: 1px solid rgba(255, 157, 46, 0.5);
  border-radius: 9px;
  background: transparent;
  color: #ffc38d;
  font-size: 12px;
  letter-spacing: 2px;
  cursor: pointer;
  transition: 0.2s;
}
.rec-go:hover {
  box-shadow: 0 0 22px rgba(255, 157, 46, 0.55);
}
</style>
