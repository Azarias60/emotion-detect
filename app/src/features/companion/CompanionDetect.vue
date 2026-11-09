<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import DetectionShell from '@/components/DetectionShell.vue'
import { useLiveTalking } from './useLiveTalking'
import { useAvatarStudio } from './useAvatarStudio'

const {
  connection,
  connectionTitle,
  connectionDetail,
  sessionId,
  listening,
  busy,
  speaking,
  emotion,
  recommendation,
  clearance,
  pipeline,
  messages,
  personas,
  activePersona,
  assets,
  currentAvatar,
  dancing,
  needPlayTap,
  init,
  tapPlay,
  sendText,
  interrupt,
  toggleMic,
  selectPreset,
  retry,
  loadAssets,
  generateAvatar,
  dance,
} = useLiveTalking()

const videoEl = ref(null)
const listRef = ref(null)
const draft = ref('')
const captionFresh = ref(false) // 字幕条是否还在展示“刚说的内容”（停止 10 秒后回引导语）
let captionTimer = null

// ── 照片制作数字人 ──
const studio = useAvatarStudio()
const {
  jobStatus, jobStageIndex, jobTotal, jobMessage,
  uploading, creating, error: studioError,
  uploadPhoto, createJob, resumeRunning, stageLabel, reset: studioReset,
} = studio

// ── 折叠栏目：选择类面板默认收起，点标题拉出（2026-10-26 用户要求页面简洁）──
const fold = reactive({ ability: false, studio: false })
const presetsOpen = ref(false)

// ── 资料卡：引擎在回复流里推 doc（重要资料）/ recommend（连续同情绪）事件驱动
// 情绪触发的推荐（连续两句同情绪）直接复用资料框展示
/** 轻提示：往聊天流里插一条不打断的灰字（与引擎的 action 行同款样式） */function pushHint(text) {
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'action' && last.content === text) return
  messages.value.push({ role: 'action', content: text })
  nextTick(() => {
    if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
  })
}

// 情绪变化：给用户一条轻提示（只在情绪翻转时出现，连续同情绪不重复刷屏）
watch(emotion, (e) => {
  if (!e || !e.changed || !e.hint) return
  pushHint(e.hint)
})

watch(recommendation, (rec) => {
  // 情绪触发的模块推荐：本页不再弹资料框（2026-10-26 用户拍板删除），
  // 只清掉状态即可——推荐改由暖暖语音里口头表达
  if (rec) clearance()
})

// 情绪徽章：只在有效情绪且非平静时展示
const emotionBadge = computed(() => {
  const e = emotion.value
  if (!e || !e.key || e.key === 'neutral') return null
  return e
})

const photoInput = ref(null)
const photoUrl = ref('') // 图片直链输入
const studioName = ref('') // 用户给数字人起的中文名
const showNameBox = ref(false)

function pickPhoto() {
  photoInput.value?.click()
}

async function onPhotoChosen(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const jobId = await uploadPhoto(file)
  if (jobId) showNameBox.value = true
}

/** 直链抓取：后端下载 → 与上传同一路径进入起名环节 */
async function confirmUrlCreate() {
  const jobId = await fetchPhotoByUrl(photoUrl.value)
  if (jobId) {
    showNameBox.value = true
    photoUrl.value = ''
  }
}

async function confirmCreate() {
  const ascii = `user${Date.now().toString(36)}`
  const okJob = await createJob(ascii, studioName.value.trim() || '我的数字人')
  if (okJob) {
    showNameBox.value = false
    studioName.value = ''
  }
}

function dismissStudio() {
  studioReset()
  showNameBox.value = false
}

onMounted(() => {
  init(videoEl.value)
  resumeRunning()
})

onBeforeUnmount(() => {
  if (captionTimer) clearTimeout(captionTimer)
  // useLiveTalking 内部已统一 teardown
})

const statusPill = computed(() => {
  if (connection.value !== 'connected') return connectionTitle.value
  if (speaking.value) return '说话中'
  if (busy.value) return '思考中'
  if (listening.value) return '聆听中'
  return '待机'
})

const caption = computed(() => {
  const last = messages.value[messages.value.length - 1]
  // 只在“刚说完”的时间窗内显示回复字幕；待机超时后回引导语，
  // 避免待机时画面里挂着上一句台词，被误读成正在播报
  if (captionFresh.value && last && last.role === 'assistant' && last.content) {
    const t = last.content.trim()
    return t.length > 28 ? t.slice(0, 28) + '…' : t
  }
  if (last && last.role === 'user') return '我正在想怎么回答…'
  return '点麦克风直接说，或输入文字'
})

const activeStep = computed(() => (speaking.value ? 4 : pipeline.value))
const rounds = computed(() => messages.value.filter((m) => m.role === 'user').length)

// ── 数字人（形象 + 音色配对）── 一项就是一对，点了两个一起换 ──
const presets = computed(() => assets.value?.presets || [])
const activePresetId = computed(() => {
  const cur = currentAvatar.value || assets.value?.defaultAvatar || ''
  return (
    presets.value.find((p) => p.avatar === cur && p.voice === assets.value?.activeVoice)?.id || ''
  )
})
const presetMixHint = computed(() => {
  if (!presets.value.length || activePresetId.value) return ''
  const cur = currentAvatar.value || assets.value?.defaultAvatar || ''
  return `当前混搭（${cur} + ${assets.value?.activeVoice || '--'}），点上面任意一项配成一对`
})
const activeVoiceName = computed(
  () =>
    (assets.value?.voices || []).find((v) => v.id === assets.value?.activeVoice)?.name ||
    assets.value?.activeVoice ||
    '--',
)
const activePreset = computed(
  () => presets.value.find((p) => p.id === activePresetId.value) || null,
)
// 左栏标题用的当前数字人名：选了预设就跟预设走（否则回退第一个人设名），
// 避免视频里已经是「鬼子」、标题还写着「小C」
const stageName = computed(
  () => activePreset.value?.name || personas.value[0]?.name || '小C',
)
// 当前数字人（右栏展示用）：配对中显示「名字 · 音色」，混搭时显示「形象 + 音色」
const currentPersona = computed(() => {
  if (activePreset.value) return `${activePreset.value.name} · ${activeVoiceName.value}`
  const cur = currentAvatar.value || assets.value?.defaultAvatar || '--'
  return `混搭：${cur} + ${activeVoiceName.value}`
})
// 当前音色（引擎表用）：配对中点名是哪个预设的音色，混搭时显示音色本身
const voiceLabel = computed(() => {
  if (activePreset.value) return `${activePreset.value.name} 的音色`
  return activeVoiceName.value
})

// ── 素材工场（LiveTalking 的投递/生成/舞蹈）──
const dropRows = computed(() => {
  const jobs = new Map((assets.value?.generations || []).map((j) => [j.name, j]))
  return (assets.value?.dropVideos || [])
    .filter((item) => !(item.built && !jobs.has(item.name))) // 已生成且无进行中任务的不占位
    .map((item) => {
      const job = jobs.get(item.name)
      let statusText = '待生成'
      if (job && job.status === 'running') statusText = '生成中…'
      else if (job && job.status === 'error') statusText = job.message || '生成失败'
      return { name: item.name, file: item.file, built: !!item.built, statusText }
    })
})
const canDance = computed(
  () => (assets.value?.avatars || []).find((a) => a.id === currentAvatar.value)?.dance === true,
)

function onPreset(id) {
  if (!busy.value) selectPreset(id)
}

function refreshAssets() {
  loadAssets()
}

function onGenerate(name) {
  generateAvatar(name)
}

function scrollBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function submit() {
  const text = draft.value
  if (!text.trim() || busy.value || connection.value !== 'connected') return
  draft.value = ''
  if (sendText(text)) {
    scrollBottom()
  }
}

// 回复流式回来时持续滚动到底部；同时刷新字幕条的“刚说完”时间窗
watch(
  () => messages.value.length && messages.value[messages.value.length - 1]?.content,
  () => {
    scrollBottom()
    captionFresh.value = true
    if (captionTimer) clearTimeout(captionTimer)
    captionTimer = setTimeout(() => {
      captionFresh.value = false
    }, 10000)
  },
)
</script>

<template>
  <DetectionShell title="情感寄托数字人" subtitle="REAL-TIME DIGITAL COMPANION">
    <div class="companion-dashboard">
      <div class="dashboard-main">
        <!-- 左：实时数字人画面（WebRTC + wav2lip 实时口型） -->
        <section class="tech-panel avatar-panel">
          <div class="panel-heading live-heading">
            <span class="live-dot" :class="{ on: connection === 'connected' }"></span>数字人实时画面
            <small>{{ stageName }} · 在线</small>
          </div>
          <div class="avatar-stage">
            <div class="hologram-stage">
              <video ref="videoEl" class="rt-video" playsinline autoplay></video>
              <div class="stage-pill" :class="{ busy: speaking || busy }">{{ statusPill }}</div>
              <div class="stage-caption">{{ caption }}</div>
              <div v-if="needPlayTap" class="play-tap" @click="tapPlay">
                <span></span>点击开播
              </div>
              <div v-if="connection !== 'connected'" class="stage-mask">
                <div class="boot-spinner" v-if="connection !== 'failed'"></div>
                <p>{{ connectionDetail }}</p>
                <button v-if="connection === 'failed'" class="retry" @click="retry">重试连接</button>
              </div>
            </div>
          </div>
          <div class="avatar-state">
            <span :class="{ busy: speaking || busy }"></span>
            <b v-if="speaking || busy">{{ statusPill }}</b>
            <small v-if="connection !== 'connected'">{{ connectionDetail }}</small>
          </div>
        </section>

        <!-- 中：实时对话 -->
        <section class="tech-panel capability-panel">
          <div class="panel-heading">
            实时对话
            <small v-if="emotionBadge" class="emo-chip" :class="emotionBadge.key">
              {{ emotionBadge.label }}<i v-if="emotionBadge.consecutive > 1">×{{ emotionBadge.consecutive }}</i>
            </small>
          </div>

          <div class="preset-bar" v-if="presets.length">
            <button class="preset-toggle" @click="presetsOpen = !presetsOpen">
              <small>数字人</small>
              <b>{{ activePreset?.name || currentAvatar || '默认形象' }}</b>
              <i class="fold-arrow" :class="{ open: presetsOpen }">▸</i>
            </button>
            <div class="preset-row" v-show="presetsOpen">
              <button
                v-for="p in presets"
                :key="p.id"
                :class="{ active: p.id === activePresetId }"
                :disabled="!p.ready || busy"
                :title="p.ready ? `点击切换到「${p.name}」` : (p.reason || '该数字人素材还没准备好')"
                @click="onPreset(p.id)"
              >
                <span class="chip-name">{{ p.name }}</span>
              </button>
              <em v-if="presetMixHint">{{ presetMixHint }}</em>
              <button class="chip-refresh" title="重新读取数字人列表" @click="refreshAssets">刷新</button>
            </div>
          </div>

          <div class="chat-console" ref="listRef">
            <div v-if="!messages.length" class="empty-hint">
              和数字人开始聊天吧
            </div>
            <div v-for="(msg, i) in messages" :key="i" class="message" :class="msg.role">
              <span v-if="msg.role !== 'action'">{{ msg.role === 'assistant' ? 'AI' : '我' }}</span>
              <p v-if="msg.role !== 'action'">{{ msg.content }}</p>
              <em v-else>{{ msg.content }}</em>
            </div>
          </div>

          <div class="input-area">
            <input
              v-model="draft"
              placeholder="跟小C说点什么…"
              :disabled="busy || connection !== 'connected'"
              @keyup.enter="submit"
            />
            <button class="mic" :class="{ on: listening }" :disabled="connection !== 'connected'" :aria-pressed="listening" @click="toggleMic">
              <span class="mic-dot" aria-hidden="true"></span>{{ listening ? '正在录音 · 点击关闭' : '开启麦克风' }}
            </button>
            <button class="send" :disabled="busy || !draft.trim() || connection !== 'connected'" @click="submit">
              发送
            </button>
            <button class="cut" :disabled="!(busy || speaking)" @click="interrupt('button')">打断</button>
          </div>
        </section>

        <!-- 右：状态与说明 -->
        <aside class="insight-column">
          <section class="tech-panel score-panel">
            <div class="panel-heading">运行状态</div>
            <div class="state-rows">
              <div>
                <small>连接状态</small>
                <strong>
                  {{ connectionTitle }}
                  <button v-if="connection === 'failed'" class="retry-inline" @click="retry">重试</button>
                </strong>
              </div>
              <div><small>当前数字人</small><strong class="persona-cell">{{ currentPersona }}</strong></div>
              <div><small>当前状态</small><strong>{{ statusPill }}</strong></div>
              <div><small>对话轮次</small><strong>{{ rounds }}</strong></div>
              <div v-if="emotionBadge">
                <small>当前情绪</small>
                <strong :class="`emo-${emotionBadge.key}`">
                  {{ emotionBadge.label }}<i v-if="emotionBadge.consecutive > 1" class="emo-count">×{{ emotionBadge.consecutive }}</i>
                </strong>
              </div>
            </div>
          </section>
          <section class="tech-panel advice-panel fold-panel" :class="{ open: fold.ability }">
            <button class="panel-heading fold-head" @click="fold.ability = !fold.ability">
              <span>能力</span>
              <i class="fold-arrow">▸</i>
            </button>
            <div class="engine-list" v-show="fold.ability">
              <div><b>口型</b><small>说话即动 · 实时同步</small></div>
              <div><b>声音</b><small>{{ voiceLabel }}</small></div>
              <div><b>听力</b><small>你说的每句话都听得清</small></div>
              <div><b>大脑</b><small>智能对话 · 边想边说</small></div>
            </div>
          </section>
          <!-- 素材工场：投递视频一键生成新形象 + 舞蹈（搬自 LiveTalking 原版数字人面板；默认收起，点标题拉出） -->
          <section class="tech-panel advice-panel fold-panel" :class="{ open: fold.studio }">
            <button class="panel-heading fold-head" @click="fold.studio = !fold.studio">
              <span>制作数字人</span>
              <i class="fold-arrow">▸</i>
            </button>
            <div class="asset-body" v-show="fold.studio">
              <template v-if="dropRows.length">
                <div v-for="row in dropRows" :key="row.name" class="asset-row">
                  <span class="drop-name" :title="row.file">{{ row.file }}</span>
                  <span class="drop-status" :class="{ running: row.statusText === '生成中…', error: row.statusText.startsWith('生成失败') }">
                    {{ row.built && row.statusText === '待生成' ? '已生成' : row.statusText }}
                  </span>
                  <button class="asset-btn" :disabled="row.statusText === '生成中…'" @click="onGenerate(row.name)">
                    {{ row.built ? '重新生成' : '生成人物' }}
                  </button>
                </div>
              </template>
              <p v-else class="asset-hint">暂无可制作的素材，点「刷新」试试</p>
              <!-- 上传照片制作数字人：点击位 → 选照片 → 起名 → 后台产线 → 完成后刷新选择 -->
              <div class="studio-block">
                <button class="studio-entry" :disabled="uploading || creating || jobStatus === 'running'" @click="pickPhoto">
                  ＋ 上传照片制作数字人
                </button>
                <input ref="photoInput" type="file" accept="image/jpeg,image/png" style="display: none" @change="onPhotoChosen" />
                <div class="studio-url">
                  <input v-model="photoUrl" placeholder="或粘贴图片直链（http/https）" @keyup.enter="confirmUrlCreate" />
                  <button class="asset-btn" :disabled="uploading || creating || jobStatus === 'running' || !photoUrl.trim()" @click="confirmUrlCreate">
                    {{ uploading ? '抓取中…' : '抓取' }}
                  </button>
                </div>
                <div v-if="showNameBox && jobStatus !== 'running'" class="studio-name">
                  <input v-model="studioName" maxlength="10" placeholder="给数字人起个名字（可留空）" @keyup.enter="confirmCreate" />
                  <button class="asset-btn" :disabled="creating" @click="confirmCreate">开始制作</button>
                </div>
                <div v-if="uploading" class="studio-progress">正在获取照片…</div>
                <div v-if="jobStatus === 'running'" class="studio-progress">
                  <div class="studio-bar">
                    <div class="studio-bar-fill" :style="{ width: `${((jobStageIndex + 1) / jobTotal) * 100}%` }"></div>
                  </div>
                  <small>{{ stageLabel() }}（{{ jobStageIndex + 1 }}/{{ jobTotal }}）—— {{ jobMessage }}</small>
                </div>
                <div v-if="jobStatus === 'completed'" class="studio-progress studio-done">
                  制作完成！点上方「刷新」，在「数字人」里就能选到新数字人。
                  <button class="asset-btn" @click="dismissStudio">知道了</button>
                </div>
                <div v-if="jobStatus === 'failed' || studioError" class="studio-progress studio-err">
                  {{ studioError || jobMessage }}
                  <button class="asset-btn" @click="dismissStudio">关闭</button>
                </div>
              </div>
              <div v-if="canDance" class="asset-row">
                <span class="drop-name">舞蹈段</span>
                <span class="drop-status">当前形象已内置</span>
                <button class="asset-btn" :disabled="!sessionId || connection !== 'connected' || dancing" @click="dance">
                  {{ dancing ? '准备中…' : '跳一段舞' }}
                </button>
              </div>
            </div>
          </section>
          <section class="tech-panel advice-panel">
            <div class="panel-heading">陪伴服务说明</div>
            <div class="advice-body">
              <p>AI 陪伴内容仅供参考，不能替代专业医生的建议。</p>
            </div>
          </section>
        </aside>
      </div>
    </div>
  </DetectionShell>
</template>

<style scoped>
.companion-dashboard {
  max-width: 1600px;
  margin: 0 auto;
  height: calc(100vh - 118px);
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  gap: 14px;
}
.dashboard-main {
  display: grid;
  /* 宽屏（1920/2K）下三栏按 1fr/1.3fr/0.7fr 无限拉伸会让竖版视频两侧留大片空白、
     对话区空旷失衡。改为固定上下限：视频栏 300~440、对话栏弹性、状态栏 240~330。 */
  grid-template-columns: minmax(300px, 440px) minmax(420px, 1fr) minmax(240px, 330px);
  /* 必须声明行高：不声明时隐施行按内容撑高，三栏会被顶出容器（输入区掉到页面外） */
  grid-template-rows: minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}
.avatar-panel,
.capability-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* ── 实时画面舞台（与首页全息舞台同一套视觉语言） ── */
.avatar-stage {
  flex: 1;
  min-height: 0;
  position: relative;
  display: grid;
  place-items: center;
  padding: 12px;
  background: radial-gradient(circle, rgba(0, 242, 255, 0.1), transparent 48%);
}
.hologram-stage {
  position: relative;
  /* 高度用视口相对值：写死 px 会成为网格项的最小内容高度，把整行撑高、
     输入区被顶到页面外（曾出现发送按钮被底部流程条盖住） */
  height: min(100%, 58vh);
  aspect-ratio: 9 / 16;
  max-width: 100%;
  background: #000;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 0 40px rgba(0, 140, 200, 0.18);
}
/* 顶部投影光束（真人视频上也要看得出来，alpha 比首页卡通版高） */
.hologram-stage::before {
  content: '';
  position: absolute;
  left: 6%;
  right: 6%;
  top: 0;
  height: 36%;
  background: linear-gradient(to bottom, rgba(0, 215, 255, 0.52), rgba(0, 200, 255, 0.14) 55%, transparent 100%);
  pointer-events: none;
  z-index: 2;
}
/* 全息边框 + 脚下辉光 */
.hologram-stage::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 16px;
  border: 2px solid rgba(0, 242, 255, 0.78);
  box-shadow:
    0 0 32px rgba(0, 180, 255, 0.4),
    inset 0 0 46px rgba(0, 120, 180, 0.16);
  background: radial-gradient(ellipse 55% 17% at 50% 91%, rgba(0, 215, 255, 0.6), transparent 72%);
  pointer-events: none;
  z-index: 3;
}
.rt-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  z-index: 1;
}
.stage-pill {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid rgba(0, 242, 255, 0.55);
  border-radius: 20px;
  background: rgba(0, 14, 26, 0.85);
  color: #9fecf2;
  font-size: 11px;
  letter-spacing: 1px;
  white-space: nowrap;
}
.stage-pill::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #00efbc;
  box-shadow: 0 0 8px #00efbc;
}
.stage-pill.busy::before {
  background: #ff9d2e;
  box-shadow: 0 0 8px #ff9d2e;
  animation: pulse 1.2s infinite;
}
/* 字幕：悬浮小药丸，不贴舞台边缘——通长渐变底会盖住舞台底边框和脚下辉光。
   宽度按“两行 × 14 字”设计，JS 侧同步把文案截到 28 字，保证内容永不超出盒高
   （line-clamp 只限盒高、会把超出的第三行齐平切掉半行字）。 */
.stage-caption {
  position: absolute;
  left: 5%;
  right: 5%;
  bottom: 12px;
  z-index: 4;
  text-align: center;
  color: #eafeff;
  font-size: 12px;
  line-height: 1.55;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.95);
  padding: 8px 12px;
  background: rgba(0, 8, 16, 0.92);
  border: 1px solid rgba(0, 242, 255, 0.22);
  border-radius: 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stage-mask {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 14px;
  padding: 24px;
  background: rgba(0, 8, 16, 0.88);
  text-align: center;
}
.stage-mask p {
  margin: 0;
  color: #8fd6dc;
  font-size: 13px;
  line-height: 1.7;
  max-width: 220px;
}
.boot-spinner {
  width: 34px;
  height: 34px;
  border: 2px dotted rgba(0, 242, 255, 0.5);
  border-radius: 50%;
  animation: spin 1.6s linear infinite;
}
.retry {
  padding: 8px 20px;
  border: 1px solid rgba(0, 242, 255, 0.55);
  background: rgba(0, 60, 85, 0.6);
  color: #bdf0f4;
  font-size: 12px;
  letter-spacing: 2px;
  cursor: pointer;
}
.play-tap {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 10px;
  background: rgba(0, 8, 16, 0.6);
  color: #d4f8fa;
  font-size: 13px;
  letter-spacing: 2px;
  cursor: pointer;
}
.play-tap span {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 1px solid rgba(0, 242, 255, 0.7);
  background: rgba(0, 60, 85, 0.7);
  position: relative;
}
.play-tap span::after {
  content: '';
  position: absolute;
  left: 17px;
  top: 13px;
  border-left: 14px solid #9fecf2;
  border-top: 9px solid transparent;
  border-bottom: 9px solid transparent;
}

/* ── 流程条 / 人设 / 对话 ── */
.avatar-state {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  padding: 10px 16px;
  border-top: 1px solid rgba(0, 242, 255, 0.14);
  color: #8fd6dc;
  font-size: 12px;
}
.avatar-state span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #00efbc;
  box-shadow: 0 0 8px #00efbc;
}
.avatar-state span.busy {
  background: #ff9d2e;
  box-shadow: 0 0 8px #ff9d2e;
  animation: pulse 1.2s infinite;
}
/* 连接信息推到行尾并降一档亮度，与状态标签分开 */
.avatar-state small {
  margin-left: auto;
  color: #7fa8b0;
  font-size: 12px;
  letter-spacing: 0.5px;
}
.pipeline {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px 4px;
  color: #7c9db4;
  font-size: 12px;
  letter-spacing: 1px;
}
.pipeline span {
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: #7c9db4;
  font-size: 12px;
  transition: 0.25s;
}
.pipeline span.on {
  color: #d4f8fa;
  background: rgba(0, 242, 255, 0.2);
  border: 1px solid rgba(0, 242, 255, 0.55);
  box-shadow: 0 0 14px rgba(0, 200, 255, 0.35);
}
.pipeline b {
  color: rgba(0, 242, 255, 0.5);
}
/* 行首「链路」标签：说明这是实时状态灯，不是可点芯片 */
.pipeline em {
  color: #4f7b84;
  font-style: normal;
  margin-right: 2px;
}
/* 数字人（形象+音色配对）选择：折叠拉手 + 展开芯片行 */
.preset-bar {
  flex: none;
}
.preset-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 16px 7px;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: #cfe8ff;
  font-size: 12.5px;
  text-align: left;
}
.preset-toggle small { color: #7c9db4; font-size: 12px; }
.preset-toggle b { font-weight: 600; letter-spacing: 0.5px; }
.preset-toggle:hover b { color: #ffffff; }
.preset-toggle .fold-arrow { margin-left: auto; }
.preset-toggle .fold-arrow.open { transform: rotate(90deg); }
.preset-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px 2px;
}
.preset-row small {
  color: #7c9db4;
  font-size: 12px;
}
.preset-row button {
  padding: 5px 14px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  background: rgba(0, 25, 40, 0.6);
  color: #9fecf2;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
.preset-row button.active {
  border-color: rgba(0, 242, 255, 0.9);
  background: rgba(0, 242, 255, 0.16);
  color: #eafeff;
  box-shadow: 0 0 14px rgba(0, 200, 255, 0.4);
}
.preset-row button:hover:not(:disabled) {
  filter: brightness(1.3);
}
.preset-row button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
/* 选中标记：✓ 前缀放在 chip-name 前（flex-column 下不会单独占一行） */
.preset-row button.active .chip-name::before {
  content: '✓ ';
  color: #00efbc;
  font-weight: 700;
}
.chip-note {
  font-size: 12px;
  color: #7fa8b0;
  letter-spacing: 0;
  line-height: 1.3;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preset-row em {
  color: #ffb45c;
  font-size: 11px;
  font-style: normal;
  letter-spacing: 0.5px;
}
.chip-refresh {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px dashed rgba(0, 242, 255, 0.4);
  background: transparent;
  color: #7fa8b0;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
}
.chip-refresh:hover {
  color: #bdf0f4;
  border-color: rgba(0, 242, 255, 0.7);
}
.empty-hint {
  margin: auto;
  color: #7fa8b0;
  font-size: 12px;
  text-align: center;
  line-height: 1.8;
}
.chat-console {
  flex: 1;
  min-height: 160px;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.message {
  display: flex;
  gap: 10px;
  max-width: 88%;
}
.message > span {
  flex: none;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(0, 242, 255, 0.4);
  color: #00f2ff;
  font-size: 10px;
  background: rgba(0, 25, 40, 0.8);
}
.message p {
  margin: 0;
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.65;
  border: 1px solid rgba(0, 242, 255, 0.14);
  background: rgba(0, 22, 36, 0.7);
  color: #d4f8fa;
  border-radius: 2px 10px 10px 10px;
  white-space: pre-wrap;
}
.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.message.user > span {
  border-color: rgba(255, 157, 46, 0.5);
  color: #ffb45c;
}
.message.user p {
  border-color: rgba(255, 157, 46, 0.2);
  background: rgba(46, 30, 8, 0.55);
  color: #ffe3c2;
  border-radius: 10px 2px 10px 10px;
}
.message.action {
  align-self: center;
  max-width: 100%;
}
.message.action em {
  color: #4f8f99;
  font-size: 11px;
  font-style: normal;
  letter-spacing: 1px;
}
.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 16px 16px;
  border-top: 1px solid rgba(0, 242, 255, 0.14);
}
.input-area input {
  flex: 1;
  min-width: 0;
  padding: 10px 14px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  background: rgba(0, 18, 30, 0.85);
  color: #d4f8fa;
  font-size: 13px;
  outline: none;
}
.input-area input:focus {
  border-color: rgba(0, 242, 255, 0.65);
}
.input-area button {
  flex: none;
  padding: 10px 14px;
  border: 1px solid rgba(0, 242, 255, 0.4);
  background: rgba(0, 40, 60, 0.7);
  color: #9fecf2;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
}
.input-area button:hover:not(:disabled) {
  filter: brightness(1.35);
}
.input-area button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
/* 禁用态保住青色描边轮廓：与面板背景仍有边界，不会看成“按钮没了” */
.input-area .send:disabled {
  color: #6f9aa3;
  border-color: rgba(0, 242, 255, 0.3);
}
/* 打断恒可见：未激活时降透明度但保留橙色边框，让用户预先知道有这个能力 */
.input-area .cut:disabled {
  opacity: 0.45;
}
.input-area .mic.on {
  border-color: rgba(255, 157, 46, 0.65);
  background: rgba(255, 157, 46, 0.16);
  color: #ffc879;
}
/* 麦克风录音状态点：灰=关，橙色脉动=正在录音 */
.mic-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #6f9aa3;
  flex: none;
}
.input-area .mic.on .mic-dot {
  background: #ff9d2e;
  box-shadow: 0 0 8px #ff9d2e;
  animation: pulse 1.2s infinite;
}
.input-area .send {
  border-color: rgba(0, 242, 255, 0.55);
  background: rgba(0, 60, 85, 0.6);
  color: #bdf0f4;
}
.input-area .cut {
  border-color: rgba(255, 157, 46, 0.5);
  color: #ffb45c;
}

/* ── 右栏 ── */
/* ── 右栏：各面板纵向排布；列整体可滚动，任何面板都不被裁切 ── */
.insight-column {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
}
/* 右栏面板头局部收紧（不改共享 DetectionShell 的 .panel-heading），
   目标是四块面板在 720p 下一屏放得下、不用滚动 */
.insight-column .panel-heading {
  padding: 6px 12px;
}
.insight-column > section {
  flex: none;
}
.state-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 12px;
}
.state-rows > div {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-bottom: 4px;
  border-bottom: 1px dashed rgba(0, 242, 255, 0.12);
}
.state-rows small {
  color: #7c9db4;
  font-size: 12px;
}
.state-rows strong {
  color: #bdf0f4;
  font-size: 12px;
  text-align: right;
}
.state-rows .mono {
  font-family: Consolas, monospace;
  color: #73f8d0;
}
.engine-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 12px;
}
.engine-list > div {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 4px;
  border-bottom: 1px dashed rgba(0, 242, 255, 0.12);
}
.engine-list b {
  color: #9fecf2;
  font-size: 12px;
  font-weight: 600;
}
.engine-list small {
  color: #9ec6cd;
  font-size: 12px;
  text-align: right;
}
/* 素材工场（投递视频一键生成 + 舞蹈）：行式布局复用 engine-list */
.asset-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 8px 12px;
}
.asset-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 4px;
  border-bottom: 1px dashed rgba(0, 242, 255, 0.12);
}
.asset-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.drop-name {
  flex: 1;
  min-width: 0;
  color: #9ec6cd;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drop-status {
  flex: none;
  color: #7c9db4;
  font-size: 12px;
}
.drop-status.running {
  color: #ffc879;
}
.drop-status.error {
  color: #ff8d6b;
}
.asset-btn {
  flex: none;
  padding: 4px 10px;
  border: 1px solid rgba(0, 242, 255, 0.4);
  background: rgba(0, 40, 60, 0.7);
  color: #9fecf2;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
}
.asset-btn:hover:not(:disabled) {
  filter: brightness(1.3);
}
.asset-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.asset-hint {
  margin: 0;
  color: #7c9db4;
  font-size: 12px;
  line-height: 1.6;
}
/* ── 折叠栏目：标题即拉手，默认收起，点击拉出（2026-10-26 页面简洁化）── */
.fold-head {
  /* 继承 .panel-heading 的深色标题条样式（:deep 规则对 button 同样生效），
     这里只补按钮重置与拉手手感 */
  width: 100%;
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.fold-head:hover { background: rgba(30, 58, 95, 0.4); }
.fold-arrow {
  font-style: normal;
  font-size: 12px;
  color: #6f9cc9;
  transition: transform 0.18s ease;
}
.fold-panel.open .fold-arrow { transform: rotate(90deg); }

/* ── 上传照片制作数字人 ── */
.studio-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(0, 242, 255, 0.14);
}
.studio-entry {
  width: 100%;
  padding: 5px 10px;
  border: 1px solid rgba(0, 242, 255, 0.45);
  border-radius: 6px;
  background: rgba(0, 60, 85, 0.45);
  color: #bdf0f4;
  font-size: 12px;
  cursor: pointer;
  transition: 0.2s;
}
.studio-entry:hover:not(:disabled) {
  filter: brightness(1.3);
}
.studio-entry:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
/* 直链输入行：与上传按钮同一折叠块内，窄输入框 + 抓取按钮 */
.studio-url {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.studio-url input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  border: 1px solid rgba(0, 242, 255, 0.35);
  border-radius: 6px;
  background: rgba(8, 24, 38, 0.6);
  color: #cfe8ff;
  font-size: 11.5px;
}
.studio-url input::placeholder {
  color: rgba(150, 190, 220, 0.55);
}
.studio-url input:focus {
  outline: none;
  border-color: rgba(0, 242, 255, 0.65);
}
.studio-url .asset-btn {
  flex-shrink: 0;
  padding: 5px 10px;
  font-size: 11.5px;
}
.studio-name {
  display: flex;
  gap: 6px;
}
.studio-name input {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  border-radius: 6px;
  background: rgba(0, 20, 35, 0.6);
  color: #d4f8fa;
  font-size: 12px;
}
.studio-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #8fd8e8;
  font-size: 12px;
  line-height: 1.5;
}
.studio-progress small {
  color: #7c9db4;
  font-size: 11px;
}
.studio-bar {
  height: 6px;
  border-radius: 3px;
  background: rgba(0, 40, 60, 0.8);
  overflow: hidden;
}
.studio-bar-fill {
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, #00c8ff, #73f8d0);
  transition: width 0.6s;
}
.studio-done {
  color: #73f8d0;
}
.studio-err {
  color: #ffb45c;
  word-break: break-all;
}
/* 右栏连接状态行内「重试」小按钮 */
.retry-inline {
  margin-left: 8px;
  padding: 2px 10px;
  border: 1px solid rgba(255, 157, 46, 0.55);
  background: rgba(255, 157, 46, 0.12);
  color: #ffc879;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
}
.retry-inline:hover {
  filter: brightness(1.3);
}
.state-rows .persona-cell {
  font-size: 12px;
  line-height: 1.4;
}
.advice-body {
  display: flex;
  gap: 10px;
  padding: 7px 12px;
}
.advice-body span {
  flex: none;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(0, 242, 255, 0.4);
  color: #00f2ff;
  font-size: 10px;
}
.advice-body p {
  margin: 0;
  color: #7fa8b0;
  font-size: 12px;
  line-height: 1.75;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes pulse {
  50% {
    opacity: 0.4;
  }
}

/* ════════ 风格覆盖层（2026-10-22 重构：玻璃拟态·蓝色基调·精简）════════
   只覆盖本页视觉，不动共享 DetectionShell。原则：弱化刺眼青色描边与发光，
   统一低饱和深蓝底 + 半透玻璃卡 + 细腻边框；层级靠明度不靠发光。 */
.companion-dashboard :deep(.tech-panel) {
  border: 1px solid rgba(96, 165, 250, 0.16);
  border-radius: 14px;
  background: linear-gradient(165deg, rgba(13, 32, 54, 0.72), rgba(8, 20, 36, 0.86));
  backdrop-filter: blur(14px);
  box-shadow: 0 10px 34px rgba(2, 8, 20, 0.45);
  overflow: hidden;
}
.companion-dashboard :deep(.tech-panel::before) {
  display: none; /* 去掉顶部青色荧光条 */
}
.companion-dashboard :deep(.panel-heading) {
  padding: 11px 16px;
  border-bottom: 1px solid rgba(96, 165, 250, 0.10);
  color: #cfe8ff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  background: rgba(30, 58, 95, 0.25);
}
.companion-dashboard :deep(.panel-heading small) {
  color: #6f9cc9;
}

/* 预设芯片：圆角胶囊，选中态蓝底白字 */
.preset-row button {
  border: 1px solid rgba(96, 165, 250, 0.22) !important;
  border-radius: 10px !important;
  background: rgba(15, 34, 58, 0.6) !important;
  color: #bad7f5 !important;
  transition: all 0.18s ease;
}
.preset-row button:hover:not(:disabled) {
  border-color: rgba(147, 197, 253, 0.5) !important;
  background: rgba(30, 58, 95, 0.55) !important;
}
.preset-row button.active {
  border-color: rgba(147, 197, 253, 0.65) !important;
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.45), rgba(30, 58, 95, 0.65)) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.3);
}
.preset-row .chip-refresh {
  border-radius: 10px !important;
}

/* 链路灯：蓝系 */
.pipeline span.on {
  color: #dbeafe !important;
  background: rgba(59, 130, 246, 0.22) !important;
  border: 1px solid rgba(147, 197, 253, 0.45) !important;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.28) !important;
}

/* 输入区按钮统一圆角蓝系 */
.input-area button {
  border-radius: 9px !important;
}
.input-area .send {
  border-color: rgba(96, 165, 250, 0.5) !important;
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.5), rgba(30, 58, 95, 0.6)) !important;
  color: #e0edff !important;
}

/* 状态行/引擎列表文字提亮调蓝 */
.state-rows span, .engine-list small {
  color: #7fa8d0 !important;
}
.state-rows strong, .engine-list b {
  color: #d7e9ff !important;
}

/* 制作模块按钮 */
.studio-entry {
  border: 1px solid rgba(96, 165, 250, 0.35) !important;
  border-radius: 10px !important;
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.28), rgba(15, 34, 58, 0.6)) !important;
  color: #cfe4ff !important;
}
.studio-entry:hover:not(:disabled) {
  border-color: rgba(147, 197, 253, 0.6) !important;
}

/* 聊天气泡圆角化 */
.bubble, .msg-bubble {
  border-radius: 12px !important;
}

/* 评审修复：青色残留收敛为蓝 + 禁用按钮可辨识 + 顶栏副标题/串口隐藏 */
.companion-dashboard :deep(.brand) p { display: none; }          /* 英文装饰副标题 */
.companion-dashboard :deep(.brand h1),
.companion-dashboard :deep(.brand h1 *) {
  color: #cfe8ff !important;
  text-shadow: 0 0 10px rgba(59, 130, 246, 0.35) !important;
}
.companion-dashboard :deep(.live-dot),
.companion-dashboard :deep(.live-dot.on) {
  background: #60a5fa !important;
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.8) !important;
}
.companion-dashboard :deep(.chip-refresh) {
  border: 1px solid rgba(96, 165, 250, 0.35) !important;
  color: #9fc3ec !important;
}
.companion-dashboard :deep(nav a.router-link-active) {
  border-color: rgba(147, 197, 253, 0.55) !important;
  color: #dbeafe !important;
}
.input-area button:disabled {
  opacity: 0.55 !important;
  border: 1px solid rgba(96, 165, 250, 0.25) !important;
}
/* 麦克风脉冲点蓝化 */
.input-area .mic.on { border-color: rgba(96, 165, 250, 0.6) !important; }
.avatar-state span {
  background: #60a5fa !important;
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.8) !important;
}

/* ════════ 对话区布局均衡 ════════ */
/* 对话面板：预设行固定，消息流弹性，输入区贴底 */
.capability-panel {
  display: flex;
  flex-direction: column;
  position: relative;
}
.capability-panel .preset-row {
  flex: none;
}
/* 消息流留白更舒展，末条不贴输入区 */
.capability-panel .chat-list,
.capability-panel .messages {
  padding-bottom: 8px;
}

/* ── 情绪徽章（右挂实时对话标题） ── */
.panel-heading .emo-chip {
  float: right;
  padding: 1px 9px;
  border-radius: 999px;
  border: 1px solid rgba(147, 197, 253, 0.45);
  background: rgba(59, 130, 246, 0.18);
  color: #cfe4ff;
  font-size: 10.5px;
  letter-spacing: 1px;
}
.panel-heading .emo-chip i {
  margin-left: 3px;
  font-style: normal;
  color: #93c5fd;
}
.panel-heading .emo-chip.low,
.panel-heading .emo-chip.anxious {
  border-color: rgba(251, 191, 36, 0.45);
  background: rgba(251, 191, 36, 0.14);
  color: #fde68a;
}
.panel-heading .emo-chip.angry,
.panel-heading .emo-chip.somatic {
  border-color: rgba(248, 113, 113, 0.45);
  background: rgba(248, 113, 113, 0.14);
  color: #fecaca;
}
.panel-heading .emo-chip.positive {
  border-color: rgba(52, 211, 153, 0.45);
  background: rgba(52, 211, 153, 0.14);
  color: #a7f3d0;
}

/* 右栏「当前情绪」行的配色（与标题徽章一致） */
.state-rows strong.emo-low,
.state-rows strong.emo-anxious { color: #fcd34d !important; }
.state-rows strong.emo-angry,
.state-rows strong.emo-somatic { color: #fca5a5 !important; }
.state-rows strong.emo-positive { color: #6ee7b7 !important; }
.emo-count {
  margin-left: 4px;
  font-style: normal;
  font-size: 10px;
  opacity: 0.8;
}
</style>
