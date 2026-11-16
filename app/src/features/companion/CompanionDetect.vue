<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import DetectionShell from '@/components/DetectionShell.vue'
import { useLiveTalking } from './useLiveTalking'
import { useAvatarStudio } from './useAvatarStudio'
import { useVoiceClone } from './useVoiceClone'

const {
  connection,
  connectionTitle,
  connectionDetail,
  sessionId,
  currentSegment,
  listening,
  audioBlocked,
  micLevel,
  uplinkBytes,
  engineEvents,
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
  switching,
  init,
  tapPlay,
  sendText,
  interrupt,
  toggleMic,
  openMic,
  selectPreset,
  switchVoice,
  voiceSwitchOpen,
  retry,
  loadAssets,
  generateAvatar,
  deleteAvatar,
  dance,
} = useLiveTalking()

const videoEl = ref(null)
const listRef = ref(null)
const draft = ref('')
const captionFresh = ref(false) // 字幕条是否还在展示“刚说的内容”（停止 10 秒后回引导语）
let captionTimer = null

// ── 照片制作数字人 ──
const studio = useAvatarStudio()
const voice = useVoiceClone()
const bindTarget = ref('')
const {
  jobStatus, jobStageIndex, jobTotal, jobMessage, interruptedNotice,
  uploading, creating, error: studioError,
  uploadPhoto, createJob, resumeRunning, cancelJob, stageLabel, reset: studioReset,
} = studio
const cancellingJob = ref(false)

// 任务终态：完成 → 自动刷新数字人列表（原来要用户手点“刷新”）
//         中断 → 提示重新上传（后端 2026-11-09 起会标 interrupted）
studio.onJobSettled((data) => {
  if (data.status === 'completed') {
    loadAssets()
    pushHint('新数字人已上架，数字人列表已自动刷新')
    // 制作前选了音色但没绑定（那时还没有数字人）：成品一上架就自动绑上。
    // 用提交时的 ASCII id——progress.json 的 name 是中文显示名，绑过去会 404。
    // 录音优先，没录音才用官方备用音色
    const pending = voice.voiceId.value || voice.officialVoice.value
    if (pending && !voice.boundAvatar.value) {
      bindVoiceToAvatar(studio.submittedName.value)
    }
  } else if (data.status === 'interrupted') {
    pushHint('制作任务已中断（进程不在了），请重新上传照片开始')
  }
})


/** 录完音色后立刻绑定（数字人已存在时） */
async function bindVoiceToAvatar(avatarId) {
  if (!avatarId) return
  // 录音克隆优先；没录音才用官方备用音色
  const vid = voice.voiceId.value || voice.officialVoice.value
  if (!vid) return
  const ok = await voice.chooseOfficial(vid, avatarId)
  if (ok) {
    pushHint(`音色已绑定到「${avatarId}」，在数字人列表选择它即可生效`)
    loadAssets()
  }
}

/** 制作前先录音：直接录（未起名也能先录，制作完成自动绑定） */
async function onRecordVoice() {
  await voice.loadSentences()
  await voice.startRecording()
}

/** 边录边停并上传：数字人已在列表中时传 id 立即绑定，否则先存音色待绑定 */
async function onStopRecordVoice(avatarId = '') {
  await voice.stopRecording(avatarId)
}

/** 选官方备用音色：有目标数字人就立即绑定，否则挂起到制作完成 */
async function onPickOfficialVoice(voiceId) {
  if (bindTarget.value) {
    await voice.chooseOfficial(voiceId, bindTarget.value)
    if (voice.boundAvatar.value === bindTarget.value) {
      pushHint(`官方音色已绑定到「${bindTarget.value}」`)
      loadAssets()
    }
  } else {
    await voice.chooseOfficial(voiceId)
  }
}

async function onCancelJob() {
  if (!window.confirm('确定取消这次制作吗？\n已生成的中间产物会保留，但本次任务会停止。')) return
  cancellingJob.value = true
  try {
    await cancelJob()
  } finally {
    cancellingJob.value = false
  }
}

// ── 折叠栏目：选择类面板默认收起，点标题拉出（2026-10-26 用户要求页面简洁）──
const fold = reactive({ ability: false, studio: false })

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
  if (!rec) return
  if (rec.kind === 'dance') {
    // 舞蹈邀请保留成可点的一行（2026-11-13 修复）：以前所有推荐都直接清掉，
    // 用户听到"要不要看段舞"却没有任何接受入口
    danceInvite.value = rec
    return
  }
  // 情绪触发的模块推荐：本页不弹资料框（2026-10-26 用户拍板删除），只清状态
  clearance()
})

// 情绪徽章：只在有效情绪且非平静时展示
const emotionBadge = computed(() => {
  const e = emotion.value
  if (!e || !e.key || e.key === 'neutral') return null
  return e
})

// 舞蹈邀请（引擎 recommend kind=dance）：渲染成可点击的一行，点了就跳
const danceInvite = ref(null)

// 麦克风无输入诊断（2026-11-13）：开麦后 5 秒音量始终为 0 → 提示检查权限/设备。
// 没有这条提示，用户只会看到"麦克风亮着但说什么都没反应"，无从判断是权限、
// 选错设备还是链路坏了。
const micSilent = ref(false)
let micSilentTimer = null
function clearMicSilent() {
  micSilent.value = false
  if (micSilentTimer) {
    clearTimeout(micSilentTimer)
    micSilentTimer = null
  }
}
watch(micLevel, (level) => {
  if (level > 0.02) {
    clearMicSilent()
    return
  }
  if (listening.value && !micSilentTimer) {
    micSilentTimer = setTimeout(() => {
      micSilentTimer = null
      if (listening.value && micLevel.value <= 0.02) micSilent.value = true
    }, 5000)
  }
})
watch(listening, (on) => {
  if (!on) clearMicSilent()
})

// 上行链路诊断（2026-11-13）：「音量在跳但没识别」时用它一眼定位断点——
// 音量条只证明麦克风有输入（本地音频图），PCM 上行是另一条链
const uplinkDiag = computed(() => {
  if (!listening.value) return ''
  const kb = Math.round(uplinkBytes.value / 1024)
  if (!uplinkBytes.value) return '未发送（通道未就绪）'
  if (!engineEvents.value) return `已发 ${kb}KB · 引擎无回传`
  return `已发 ${kb}KB · 引擎回传 ${engineEvents.value} 条`
})

// 中文输入法守卫（2026-11-13 修复）：@keyup.enter 在 IME 确认候选词时也会
// 触发，候选词刚上屏消息就发出去了——数字人开始回答半句话
const composing = ref(false)
function onInputEnter() {
  if (composing.value) return
  submit()
}

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
  // 麦克风常开（2026-11-11 用户要求）：进页面自动开麦，无需手点；权限未授权
  // 或浏览器策略挂起时静默降级（等首次点击自动恢复），手动按钮仍可用
  void openMic()
  void voice.loadSentences()
  void voice.loadOfficial()
})

onBeforeUnmount(() => {
  if (captionTimer) clearTimeout(captionTimer)
  clearMicSilent()
  // useLiveTalking 内部已统一 teardown
})

const statusPill = computed(() => {
  if (connection.value !== 'connected') return connectionTitle.value
  // 待机框追加当前播放片段名（idle1/greet/X_to_Y），直观展示播放逻辑
  const seg = currentSegment.value ? ' · ' + currentSegment.value : ''
  if (speaking.value) return '说话中' + seg
  if (busy.value) return '思考中'
  if (listening.value) return '聆听中' + seg
  return '待机' + seg
})

const caption = computed(() => {
  const last = messages.value[messages.value.length - 1]
  // 只在“刚说完”的时间窗内显示回复字幕；待机超时后回引导语，
  // 避免待机时画面里挂着上一句台词，被误读成正在播报
  if (captionFresh.value && last && last.role === 'assistant' && last.content) {
    const t = last.content.trim().replace(/^\[chat\]\s*/, '')
    return t.length > 28 ? t.slice(0, 28) + '…' : t
  }
  if (last && last.role === 'user') return '我正在想怎么回答…'
  return '点麦克风直接说，或输入文字'
})

// 当前片段类型（idle1/2/3=待机，X_to_Y=过渡，talk=说话，greet/comfort/encourage=动作）
const segmentKindLabel = computed(() => {
  const s = currentSegment.value || ''
  if (!s) return ''
  if (/_to_/.test(s)) return '过渡衔接'
  if (/^idle/.test(s)) return '待机'
  if (/^talk/.test(s)) return '说话'
  return '动作'
})
const activeStep = computed(() => (speaking.value ? 4 : pipeline.value))
const rounds = computed(() => messages.value.filter((m) => m.role === 'user').length)

// ── 数字人（形象 + 音色配对）── 一项就是一对，点了两个一起换 ──
const presets = computed(() => assets.value?.presets || [])
const activePresetId = computed(() => {
  const cur = currentAvatar.value || assets.value?.defaultAvatar || ''
  // 严格配对（形象+音色都对上）→ 只有形象对上也算"选了这个人"，避免下拉框
  // 因为音色不一致就显示成空（用户看来是"没选中任何数字人"）
  const exact = presets.value.find(
    (p) => p.avatar === cur && p.voice === assets.value?.activeVoice,
  )
  if (exact) return exact.id
  const byAvatar = presets.value.find((p) => p.avatar === cur && p.ready)
  return byAvatar?.id || ''
})
const presetMixHint = computed(() => {
  if (!presets.value.length || activePresetId.value) return ''
  const cur = currentAvatar.value || assets.value?.defaultAvatar || ''
  return `当前混搭（${cur} + ${assets.value?.activeVoice || '--'}），请在左侧下拉里选一项配成一对`
})
const voicePickLabel = computed(() => {
  if (voice.voiceId.value) return `我录的音色（${voice.voiceId.value}）`
  const id = voice.officialVoice.value
  if (id) {
    const found = (voice.officialVoices.value || []).find((v) => v.id === id)
    return found ? found.label : id
  }
  return '未选（制作完成后使用默认音色）'
})
const currentPresetNote = computed(() => {
  const p = presets.value.find((x) => x.id === activePresetId.value)
  if (!p) return ''
  return p.note || ''
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
  // 原来 busy 时静默 return，用户点了下拉毫无反应（看起来像坏了）。
  // 现在允许切换：正在说话/思考就先打断，再换人换音色。
  if (busy.value || speaking.value) interrupt()
  selectPreset(id)
}

// 删除数字人：删除入口在「刷新」旁边，作用于**当前正在使用**的形象。
// 先弹页内确认框，用户点「确认删除」才真正调引擎接口；
// 内置人物（zzf/yy）或当前形象不在列表里时给出提示，不弹框。
const deletingAvatar = ref('')
const deleteTarget = ref(null) // 待删除的 preset（null = 不显示确认框）
const deleteCardEl = ref(null)

// 删除确认框：Escape 关闭 + 打开时聚焦（2026-11-13，role=alertdialog 名实相符）
watch(deleteTarget, (target) => {
  if (target) nextTick(() => deleteCardEl.value?.focus())
})

function askDeleteCurrent() {
  if (deletingAvatar.value) return
  const preset = activePreset.value
    || presets.value.find((p) => p.avatar === currentAvatar.value)
    || null
  if (!preset) {
    pushHint(`当前形象（${currentAvatar.value || '未知'}）不在数字人列表里，无法删除`)
    return
  }
  if (!presetDeletable(preset)) {
    pushHint(`「${preset.name}」是内置形象，不能删除`)
    return
  }
  deleteTarget.value = preset
}

function cancelDelete() {
  if (deletingAvatar.value) return
  deleteTarget.value = null
}

async function confirmDelete() {
  const preset = deleteTarget.value
  if (!preset) return
  const label = preset.name || preset.avatar
  deletingAvatar.value = preset.avatar
  try {
    const ok = await deleteAvatar(preset.avatar, label)
    if (ok) deleteTarget.value = null
  } finally {
    deletingAvatar.value = ''
  }
}

// 内置固定预设（引擎写死的 zzf/yy）不提供删除入口
function presetDeletable(p) {
  return p?.avatar && !['my_avatar', 'yy'].includes(p.avatar)
}

// 「删除」按钮的悬停说明：告诉用户它删的是哪个、能不能删
const deleteButtonTitle = computed(() => {
  const preset = activePreset.value
    || presets.value.find((p) => p.avatar === currentAvatar.value)
    || null
  if (!preset) return '当前形象不在数字人列表里，无法删除'
  if (!presetDeletable(preset)) return `「${preset.name}」是内置形象，不能删除`
  return `删除当前数字人「${preset.name}」`
})

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

/** 接受舞蹈邀请：清掉邀请行并让数字人跳一段 */
async function acceptDance() {
  danceInvite.value = null
  await dance()
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
        <!-- 左：实时对话 -->
        <section class="tech-panel capability-panel">
          <div class="panel-heading">
            实时对话
            <small v-if="emotionBadge" class="emo-chip" :class="emotionBadge.key">
              {{ emotionBadge.label }}<i v-if="emotionBadge.consecutive > 1">×{{ emotionBadge.consecutive }}</i>
            </small>
          </div>

          <!-- 数字人选择：预设卡片栅格（形象+音色成对切换）；刷新/删除是次要操作靠右 -->
          <div class="dh-picker" v-if="presets.length">
            <div class="dh-picker-head">
              <span class="dh-picker-label">数字人</span>
              <div class="dh-toolbar-actions">
                <button class="dh-action" title="重新读取数字人列表" @click="refreshAssets">⟳ 刷新</button>
                <button
                  class="dh-action danger"
                  :disabled="!!deletingAvatar"
                  :title="deleteButtonTitle"
                  @click="askDeleteCurrent"
                >{{ deletingAvatar ? '删除中…' : '✕ 删除' }}</button>
              </div>
            </div>
            <div class="dh-select-row">
              <select
                class="dh-select"
                :value="activePresetId || ''"
                @change="onPreset($event.target.value)"
              >
                <option
                  v-for="p in presets"
                  :key="p.id"
                  :value="p.id"
                  :disabled="!p.ready"
                >{{ p.name }}{{ p.ready ? '' : '（未就绪）' }}</option>
                <option v-if="!activePresetId" value="" disabled>当前为混搭，请选择</option>
              </select>
              <span v-if="currentPresetNote" class="dh-select-note">{{ currentPresetNote }}</span>
              <button
                class="dh-action dh-voice-btn"
                :class="{ on: voiceSwitchOpen }"
                title="手动切换音色（会打破与形象的自带配对）"
                @click="voiceSwitchOpen = !voiceSwitchOpen"
              >🎙 音色</button>
            </div>
            <!-- 手动音色面板：只有点「音色」按钮才展开（人物↔音色强绑定的唯一破例入口） -->
            <div v-if="voiceSwitchOpen" class="dh-voice-panel">
              <p class="dh-voice-tip">
                默认情况下音色跟着形象走。这里手动选的音色会覆盖配对，直到你再选一次数字人。
              </p>
              <button
                v-for="v in (assets?.voices || [])"
                :key="v.id"
                class="dh-voice-item"
                :class="{ active: v.id === assets?.activeVoice }"
                @click="switchVoice(v.id)"
              >
                <span class="dh-voice-name">{{ v.name }}</span>
                <span class="dh-voice-check" v-if="v.id === assets?.activeVoice">✓ 使用中</span>
              </button>
            </div>
            <p v-if="presetMixHint" class="dh-mix-hint">{{ presetMixHint }}</p>
          </div>

          <div class="chat-console" ref="listRef">
            <!-- 消息流限宽居中：行长 60~70 字最易读，宽屏不会一条消息横跨整个面板 -->
            <div class="chat-stream">
              <div v-if="!messages.length" class="empty-hint">
                和数字人开始聊天吧
              </div>
              <div v-for="(msg, i) in messages" :key="i" class="message" :class="msg.role">
                <!-- 错误消息：红字 + 下一步操作按钮（商用惯例——错误必须给出口） -->
                <template v-if="msg.role === 'error'">
                  <em class="error-text">{{ msg.content }}</em>
                  <button
                    class="retry-inline"
                    @click="msg.retryKind === 'mic' ? toggleMic() : retry()"
                  >
                    {{ msg.retryKind === 'mic' ? '去开麦克风' : '重试' }}
                  </button>
                </template>
                <template v-else-if="msg.role !== 'action'">
                  <span class="avatar">{{ msg.role === 'assistant' ? 'AI' : '我' }}</span>
                  <p>{{ msg.content }}</p>
                </template>
                <em v-else>{{ msg.content }}</em>
              </div>
              <!-- 舞蹈邀请：可点的一行（引擎连续同情绪时发出） -->
              <div v-if="danceInvite" class="message dance-invite">
                <span class="avatar">AI</span>
                <div class="dance-invite-body">
                  <p>{{ danceInvite.reason || '要不要看段舞放松一下？' }}</p>
                  <div class="dance-invite-actions">
                    <button class="asset-btn" :disabled="dancing" @click="acceptDance">
                      {{ dancing ? '准备中…' : '好，跳一段' }}
                    </button>
                    <button class="dh-action" @click="danceInvite = null">下次吧</button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="input-area">
            <input
              v-model="draft"
              placeholder="跟小C说点什么…"
              :disabled="busy || connection !== 'connected'"
              @keyup.enter="onInputEnter"
              @compositionstart="composing = true"
              @compositionend="composing = false"
            />
            <button
              class="mic"
              :class="{ on: listening }"
              :disabled="connection !== 'connected'"
              :aria-pressed="listening"
              :title="listening ? `正在录音（音量 ${Math.round(micLevel * 100)}%）· 点击关闭` : '开启麦克风'"
              @click="toggleMic"
            >
              <span class="mic-dot" aria-hidden="true"></span>
              <!-- 实时音量条：只有亮/灭的按钮说明不了链路死活（2026-11-13 补） -->
              <span v-if="listening" class="mic-meter" aria-hidden="true">
                <i :style="{ width: Math.round(micLevel * 100) + '%' }"></i>
              </span>
              麦克风
            </button>
            <button class="send" :disabled="busy || !draft.trim() || connection !== 'connected'" @click="submit">
              发送
            </button>
            <button v-if="busy || speaking" class="cut" title="打断当前播报" @click="interrupt('button')">打断</button>
          </div>
        </section>

        <!-- 中：实时数字人画面（页面主角）（WebRTC + wav2lip 实时口型） -->
        <section class="tech-panel avatar-panel">
          <div class="panel-heading live-heading">
            <span class="live-dot" :class="{ on: connection === 'connected' }"></span>数字人实时画面
            <small>{{ stageName }} · 在线</small>
          </div>
          <div class="avatar-stage">
            <!-- 当前播放的片段：放在画面旁边的留白处（不遮挡人物） -->
            <div class="stage-segment" v-if="currentSegment">
              <span class="stage-segment-cap">正在播放</span>
              <b class="stage-segment-name">{{ currentSegment }}</b>
              <span class="stage-segment-kind">{{ segmentKindLabel }}</span>
            </div>
            <div class="hologram-stage">
              <video ref="videoEl" class="rt-video" playsinline autoplay></video>
              <div v-if="needPlayTap" class="play-tap" @click="tapPlay">
                <span></span>点击开播
              </div>
              <!-- 切换数字人的窗口期：teardown 已把视频清空但新连接还没建成，
                   connection 可能还是 connected——补一个 switching 遮罩，
                   别让用户对着黑画面以为坏了（2026-11-13） -->
              <div v-if="switching && connection === 'connected'" class="stage-mask">
                <div class="boot-spinner"></div>
                <p>{{ connectionDetail }}</p>
              </div>
              <div v-if="connection !== 'connected'" class="stage-mask">
                <div class="boot-spinner" v-if="connection !== 'failed'"></div>
                <p>{{ connectionDetail }}</p>
                <button v-if="connection === 'failed'" class="retry" @click="retry">重试连接</button>
              </div>
            </div>
          </div>
          <div class="stage-caption" :class="{ fresh: captionFresh }">{{ caption }}</div>
          <div class="avatar-state">
            <span :class="{ busy: speaking || busy }"></span>
            <!-- 状态常驻（2026-11-13）：以前待机/聆听时整行被藏掉，用户看不到
                 "聆听中/正在识别"，只剩"思考中"一种文案 -->
            <b>{{ statusPill }}</b>
            <small v-if="connection !== 'connected'">{{ connectionDetail }}</small>
          </div>
        </section>

        <!-- 右：运行状态与制作 -->
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
              <!-- 麦克风音量诊断行：看不到数值就无法判断"到底听到我没有"（2026-11-13） -->
              <div v-if="listening">
                <small>麦克风音量</small>
                <strong :class="{ 'mic-silent': micSilent || audioBlocked }">
                  {{ Math.round(micLevel * 100) }}%
                  <i v-if="audioBlocked" class="mic-silent-tip">· 浏览器挂起了采音，点页面任意处恢复</i>
                  <i v-else-if="micSilent" class="mic-silent-tip">· 没有收到声音</i>
                </strong>
              </div>
              <!-- 上行诊断：「音量在跳但没识别」时看这行（未发送=ws 断了；有发送无回传=VAD/唤醒门） -->
              <div v-if="listening && uplinkDiag">
                <small>语音上行</small>
                <strong class="diag-line">{{ uplinkDiag }}</strong>
              </div>
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
              <p v-if="interruptedNotice" class="studio-notice">{{ interruptedNotice }}</p>
              <div class="studio-block">
                <button class="studio-entry" :disabled="uploading || creating || jobStatus === 'running'" @click="pickPhoto">
                  ＋ 上传照片制作数字人
                </button>
                <input ref="photoInput" type="file" accept="image/jpeg,image/png,image/webp" style="display: none" @change="onPhotoChosen" />
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
                  <button class="asset-btn studio-cancel" :disabled="cancellingJob" @click="onCancelJob">
                    {{ cancellingJob ? '取消中…' : '取消制作' }}
                  </button>
                </div>
                <div v-if="jobStatus === 'completed'" class="studio-progress studio-done">
                  制作完成！数字人列表已自动刷新，在「数字人」里就能选到新数字人。
                  <button class="asset-btn" @click="dismissStudio">知道了</button>
                </div>
                <!-- 语音克隆：读一句引导句当素材，与照片数字人配合（零样本克隆） -->
                <div class="studio-voice">
                  <div class="studio-voice-head">🎙 为即将制作的数字人选音色</div>
                  <p class="studio-voice-hint">
                    录一段 3~15 秒的自己的声音，或用官方备用音色。选中的音色会在
                    制作完成时自动和这个新数字人绑定（之后只有手动点「音色」才能改）。
                    都不选则用它自带的默认音色。
                  </p>
                  <p class="studio-voice-current">
                    当前选择：<b>{{ voicePickLabel }}</b>
                  </p>
                  <div class="studio-voice-sentence" v-if="voice.sentence.value">
                    「{{ voice.sentence.value }}」
                    <button class="asset-btn" :disabled="voice.recording.value || voice.uploading.value" @click="voice.pickSentence()">换一句</button>
                  </div>
                  <div class="studio-voice-actions">
                    <button v-if="!voice.recording.value" class="asset-btn" :disabled="voice.uploading.value" @click="onRecordVoice()">
                      {{ voice.uploading.value ? '上传中…' : voice.voiceId.value ? '重新录音' : '开始录音' }}
                    </button>
                    <button v-else class="asset-btn studio-cancel" @click="onStopRecordVoice()">
                      停止并上传（{{ voice.seconds.value }}s）
                    </button>
                  </div>
                  <!-- 不想录音？官方备用音色（CosyVoice 一男一女），选中即绑定 -->
                  <div class="studio-voice-official">
                    <span class="studio-voice-official-label">不想录音？官方备用音色：</span>
                    <button
                      v-for="v in voice.officialVoices.value.filter((v) => v.available)"
                      :key="v.id"
                      class="asset-btn"
                      :class="{ active: voice.officialVoice.value === v.id }"
                      :disabled="voice.recording.value || voice.uploading.value"
                      @click="onPickOfficialVoice(v.id)"
                    >
                      {{ v.label }}
                    </button>
                  </div>
                  <div v-if="voice.error.value" class="studio-progress studio-err">{{ voice.error.value }}</div>
                  <div v-if="voice.done.value" class="studio-progress studio-done">{{ voice.done.value }}</div>
                  <!-- 已录音色但还没绑定：手动选一个已有数字人绑上 -->
                  <div v-if="voice.voiceId.value && !voice.boundAvatar.value" class="studio-voice-bind">
                    <select v-model="bindTarget">
                      <option value="" disabled>选择要绑定的数字人</option>
                      <option v-for="p in presets" :key="p.id" :value="p.id" :disabled="!p.ready">
                        {{ p.name }}{{ p.ready ? '' : '（素材未就绪）' }}
                      </option>
                    </select>
                    <button class="asset-btn" :disabled="!bindTarget" @click="bindVoiceToAvatar(bindTarget)">绑定音色</button>
                  </div>
                </div>
                <div v-if="jobStatus === 'interrupted'" class="studio-progress studio-err">
                  制作任务已中断（后台进程不在了，可能是后端重启或被手动结束）。请重新上传照片开始。
                  <button class="asset-btn" @click="dismissStudio">知道了</button>
                </div>
                <div v-if="(jobStatus === 'failed' || studioError) && jobStatus !== 'interrupted'" class="studio-progress studio-err">
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

    <!-- 删除数字人确认框：页内弹窗，避免误删（素材删除后不可恢复） -->
    <div v-if="deleteTarget" class="modal-mask" @click.self="cancelDelete" @keydown.esc="cancelDelete">
      <div class="modal-card" role="alertdialog" aria-modal="true" tabindex="-1" ref="deleteCardEl">
        <h3 class="modal-title">删除数字人</h3>
        <p class="modal-lead">
          确定要删除数字人「<b>{{ deleteTarget.name || deleteTarget.avatar }}</b>」吗？
        </p>
        <p class="modal-warn">
          删除后该形象的全部素材（动作段、过渡段、头像帧）与制作记录都会被清除，<b>且无法恢复</b>。
          如果只是想暂时不用，切换别的数字人即可，不必删除。
        </p>
        <p v-if="deleteTarget.avatar === currentAvatar" class="modal-note">
          注意：这是当前正在使用的形象，删除后会自动切回默认形象。
        </p>
        <div class="modal-actions">
          <button class="modal-btn ghost" type="button" :disabled="!!deletingAvatar" @click="cancelDelete">取消</button>
          <button class="modal-btn danger" type="button" :disabled="!!deletingAvatar" @click="confirmDelete">
            {{ deletingAvatar ? '删除中…' : '确认删除' }}
          </button>
        </div>
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
  /* 2026-11-12 布局重排：人物居中当主角（9:16 竖版占满中栏高度），
     对话固定左栏、状态/制作固定右栏，两侧等宽收窄让中间更突出。 */
  grid-template-columns: minmax(360px, 420px) minmax(380px, 1fr) minmax(280px, 330px);
  grid-template-rows: minmax(0, 1fr);
  gap: 16px;
  min-height: 0;
}
.avatar-panel,
.capability-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 中栏是页面主角：画面区吃满剩余高度，视频等比放大居中 */
.avatar-panel .avatar-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  /* 左右对称留白：左边放"正在播放"信息条，视频因此仍然居中 */
  padding: 0 118px;
}
.stage-segment {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  width: 102px;
  padding: 10px 10px 11px;
  border: 1px solid rgba(0, 242, 255, 0.18);
  border-radius: 9px;
  background: linear-gradient(160deg, rgba(0, 32, 48, 0.85), rgba(0, 18, 30, 0.72));
  pointer-events: none;
}
.stage-segment-cap {
  font-size: 10.5px;
  letter-spacing: 1.5px;
  color: rgba(111, 150, 163, 0.95);
}
.stage-segment-name {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.3;
  color: #9fecf2;
  word-break: break-all;
  letter-spacing: 0.3px;
}
.stage-segment-kind {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid rgba(0, 242, 255, 0.32);
  border-radius: 999px;
  font-size: 10.5px;
  color: rgba(159, 236, 242, 0.9);
}
.avatar-panel .hologram-stage {
  height: 100%;
  max-height: 100%;
  aspect-ratio: 9 / 16;
  width: auto;
  max-width: 100%; /* 外层 padding-left 已让出信息条位置 */
  margin: 0 auto;
  /* 中栏比视频宽时会有左右留白：用极淡的内阴影把留白"收"进舞台，
     视觉上不再是一块空白（不做裁切，只加背景辉光） */
  box-shadow: 0 0 60px rgba(0, 242, 255, 0.07) inset, 0 0 40px rgba(0, 0, 0, 0.35);
}
.avatar-panel .rt-video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  filter: brightness(1.06) contrast(1.04);
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
/* 字幕：画面下方的通栏条（2026-11-12 用户要求人物不被遮挡）——
   不再悬浮压在人物身上；宽度按“两行 × 14 字”设计，JS 侧同步截到 28 字。 */
.stage-caption {
  margin: 8px 12px 0;
  text-align: center;
  color: #eafeff;
  font-size: 12.5px;
  line-height: 1.55;
  padding: 7px 12px;
  background: rgba(0, 8, 16, 0.72);
  border: 1px solid rgba(0, 242, 255, 0.18);
  border-radius: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stage-caption.fresh {
  border-color: rgba(0, 242, 255, 0.4);
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
/* ════════ 数字人工具条（常驻可见，替代原折叠拉手） ════════ */
/* 布局规则：标签 → 主操作芯片组（flex:1 可换行）→ 次要操作（刷新/删除）靠右。
   次要操作永远是低对比文字按钮，不和主操作抢注意力。 */
.dh-picker {
  flex: none;
  padding: 10px 14px 12px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.12);
  background: rgba(0, 20, 33, 0.5);
}
.dh-picker-head {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}
.dh-picker-label {
  color: #6f96a3;
  font-size: 11px;
  letter-spacing: 2px;
}
.dh-select-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.dh-select {
  flex: none;
  min-width: 172px;
  max-width: 60%;
  padding: 8px 30px 8px 12px;
  border: 1px solid rgba(0, 242, 255, 0.32);
  border-radius: 8px;
  background: rgba(0, 25, 40, 0.9)
    url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236f96a3'/%3E%3C/svg%3E")
    no-repeat right 11px center;
  color: #d4f8fa;
  font-size: 13.5px;
  font-family: inherit;
  letter-spacing: 0.5px;
  cursor: pointer;
  appearance: none;
  transition: 0.18s;
  text-overflow: ellipsis;
}
.dh-select:hover:not(:disabled) {
  border-color: rgba(0, 242, 255, 0.6);
}
.dh-select:focus {
  outline: none;
  border-color: rgba(0, 242, 255, 0.8);
  box-shadow: 0 0 0 2px rgba(0, 242, 255, 0.12);
}
.dh-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.dh-select option {
  background: #041824;
  color: #d4f8fa;
}
.dh-voice-btn {
  flex: none;
  padding: 7px 12px;
  border: 1px solid rgba(0, 242, 255, 0.28);
  border-radius: 8px;
  font-size: 12.5px;
}
.dh-voice-btn.on {
  border-color: rgba(0, 242, 255, 0.75);
  background: rgba(0, 242, 255, 0.12);
  color: #eafeff;
}
.dh-voice-panel {
  margin-top: 8px;
  padding: 8px 10px 10px;
  border: 1px solid rgba(0, 242, 255, 0.2);
  border-radius: 8px;
  background: rgba(0, 18, 30, 0.7);
}
.dh-voice-tip {
  margin: 0 0 8px;
  font-size: 11.5px;
  line-height: 1.5;
  color: rgba(111, 150, 163, 0.95);
}
.dh-voice-item {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 7px 10px;
  margin-bottom: 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: rgba(0, 25, 40, 0.6);
  color: #9fecf2;
  font-size: 12.5px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: 0.15s;
}
.dh-voice-item:hover {
  border-color: rgba(0, 242, 255, 0.4);
  background: rgba(0, 242, 255, 0.08);
}
.dh-voice-item.active {
  border-color: rgba(0, 239, 188, 0.6);
  color: #eafeff;
}
.dh-voice-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dh-voice-check {
  flex: none;
  margin-left: 8px;
  font-size: 11px;
  color: #00efbc;
}
.dh-select-note {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  line-height: 1.45;
  color: rgba(111, 150, 163, 0.95);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dh-toolbar-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}
.dh-action {
  padding: 5px 10px;
  border: 0;
  background: transparent;
  color: #6f96a3;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  border-radius: 4px;
  transition: 0.18s;
}
.dh-action:hover:not(:disabled) {
  color: #bdf0f4;
  background: rgba(0, 242, 255, 0.1);
}
.dh-action.danger {
  color: #c97b7b;
}
.dh-action.danger:hover:not(:disabled) {
  color: #ffb4b4;
  background: rgba(255, 60, 60, 0.14);
}
.dh-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.dh-mix-hint {
  margin: 0;
  padding: 6px 16px 0;
  color: #ffb45c;
  font-size: 11px;
  letter-spacing: 0.5px;
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
/* ── 删除确认弹窗（与全站深色科技风一致） ── */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 8, 14, 0.72);
  backdrop-filter: blur(3px);
}
.modal-card {
  width: min(440px, 100%);
  padding: 26px 28px 22px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  background: rgba(4, 20, 30, 0.97);
  box-shadow:
    0 0 0 1px rgba(0, 242, 255, 0.08),
    0 24px 70px rgba(0, 0, 0, 0.6),
    0 0 60px rgba(0, 242, 255, 0.1);
}
.modal-title {
  margin: 0 0 14px;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 2px;
  color: #eafcff;
}
.modal-lead {
  margin: 0 0 12px;
  font-size: 13.5px;
  line-height: 1.8;
  color: #cfe9f2;
}
.modal-lead b {
  color: #00f2ff;
  font-weight: 600;
}
.modal-warn {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-left: 2px solid rgba(255, 122, 122, 0.7);
  background: rgba(255, 92, 92, 0.08);
  font-size: 12.5px;
  line-height: 1.75;
  color: #f0c9c9;
}
.modal-warn b {
  color: #ff9d9d;
}
.modal-note {
  margin: 0 0 4px;
  font-size: 12px;
  color: #ffb45c;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
.modal-btn {
  padding: 8px 20px;
  font-size: 13px;
  letter-spacing: 2px;
  cursor: pointer;
  transition: 0.18s;
}
.modal-btn.ghost {
  border: 1px solid rgba(0, 242, 255, 0.35);
  background: transparent;
  color: #9fecf2;
}
.modal-btn.ghost:hover:not(:disabled) {
  background: rgba(0, 242, 255, 0.12);
}
.modal-btn.danger {
  border: 1px solid rgba(255, 92, 92, 0.65);
  background: rgba(255, 60, 60, 0.18);
  color: #ffb4b4;
}
.modal-btn.danger:hover:not(:disabled) {
  background: rgba(255, 60, 60, 0.32);
  color: #fff;
}
.modal-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
/* 消息气泡：AI 左 / 用户右 / 系统提示居中；头像圆形徽标，气泡圆角不对称指向头像 */
.message {
  display: flex;
  gap: 10px;
  max-width: 100%;
  align-items: flex-end;
}
/* 错误消息（role=error）：红字 + 行内"重试"操作，一眼看出坏了且知道下一步 */
.message.error {
  flex-wrap: wrap;
  align-items: center;
  color: #ff8a8a;
  font-size: 13px;
}
.message.error .error-text {
  font-style: normal;
  line-height: 1.5;
}
.message > .avatar {
  flex: none;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(96, 165, 250, 0.4);
  background: rgba(15, 34, 58, 0.9);
  color: #93c5fd;
  font-size: 10px;
  letter-spacing: 0.5px;
}
.message p {
  margin: 0;
  padding: 9px 13px;
  font-size: 13px;
  line-height: 1.65;
  max-width: min(88%, 620px);
  border: 1px solid rgba(96, 165, 250, 0.16);
  background: rgba(15, 34, 58, 0.75);
  color: #dbeafe;
  border-radius: 14px 14px 14px 4px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.message.user > .avatar {
  border-color: rgba(251, 146, 60, 0.45);
  color: #fdba74;
}
.message.user p {
  border-color: rgba(251, 146, 60, 0.22);
  background: rgba(60, 36, 14, 0.6);
  color: #ffedd5;
  border-radius: 14px 14px 4px 14px;
}
.message.action {
  align-self: center;
  max-width: 100%;
}
.message.action em {
  color: #64748b;
  font-size: 11px;
  font-style: normal;
  letter-spacing: 0.5px;
}
/* 舞蹈邀请：可点的一行（2026-11-13） */
.message.dance-invite {
  align-self: flex-start;
}
.dance-invite-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid rgba(167, 139, 250, 0.35);
  border-radius: 14px 14px 14px 4px;
  background: rgba(46, 34, 84, 0.6);
}
.dance-invite-body p {
  margin: 0;
  padding: 0;
  border: none;
  background: none;
  color: #ddd6fe;
  font-size: 13px;
}
.dance-invite-actions {
  display: flex;
  gap: 8px;
}
/* 输入区：输入框 flex:1，发送是唯一主按钮（实心），麦克风次之，打断仅在有播报时出现 */
.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 16px 16px;
  border-top: 1px solid rgba(96, 165, 250, 0.14);
  align-items: center;
}
.input-area input {
  flex: 1;
  min-width: 0;
  padding: 10px 14px;
  border: 1px solid rgba(96, 165, 250, 0.28);
  border-radius: 10px;
  background: rgba(8, 20, 36, 0.85);
  color: #dbeafe;
  font-size: 13px;
  outline: none;
  transition: 0.18s;
}
.input-area input:focus {
  border-color: rgba(96, 165, 250, 0.65);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12);
}
.input-area input::placeholder {
  color: #5b7a94;
}
.input-area button {
  flex: none;
  padding: 10px 14px;
  border: 1px solid rgba(96, 165, 250, 0.35);
  border-radius: 10px;
  background: rgba(30, 58, 95, 0.45);
  color: #bad7f5;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.18s;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}
.input-area button:hover:not(:disabled) {
  filter: brightness(1.25);
}
.input-area button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
/* 发送：唯一实心主按钮 */
.input-area .send {
  border-color: rgba(96, 165, 250, 0.6);
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.65), rgba(30, 58, 95, 0.75));
  color: #ffffff;
  font-weight: 600;
}
.input-area .send:hover:not(:disabled) {
  box-shadow: 0 4px 16px rgba(37, 99, 235, 0.3);
}
/* 麦克风：录音中变蓝，不抢发送的注意力 */
.input-area .mic.on {
  border-color: rgba(96, 165, 250, 0.6);
  background: rgba(59, 130, 246, 0.2);
  color: #dbeafe;
}
.mic-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #64748b;
  flex: none;
  transition: 0.18s;
}
.input-area .mic.on .mic-dot {
  background: #60a5fa;
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.9);
  animation: pulse 1.2s infinite;
}
/* 麦克风实时电平条（2026-11-13）：宽 26px 的细槽，内部按音量填充 */
.mic-meter {
  flex: none;
  width: 26px;
  height: 4px;
  border-radius: 2px;
  background: rgba(96, 165, 250, 0.18);
  overflow: hidden;
}
.mic-meter i {
  display: block;
  height: 100%;
  width: 0;
  border-radius: 2px;
  background: linear-gradient(90deg, #38bdf8, #34d399);
  transition: width 0.12s linear;
}
/* 麦克风无输入提示（开麦 5 秒仍无音量） */
.state-rows strong.mic-silent {
  color: #fbbf24;
}
.mic-silent-tip {
  font-style: normal;
  font-size: 10.5px;
  opacity: 0.85;
}
/* 上行诊断行：比状态行更小更暗，属于技术诊断信息 */
.state-rows strong.diag-line {
  font-size: 11.5px;
  color: #7fa8b0;
  font-weight: 500;
}
/* 打断：低透明度幽灵按钮，只在有播报时出现 */
.input-area .cut {
  border-color: rgba(251, 146, 60, 0.45);
  background: transparent;
  color: #fdba74;
  opacity: 0.85;
}
.input-area .cut:hover {
  opacity: 1;
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
/* 中断任务提示条（有旧任务进程已不在时显示，不接、不假跑） */
.studio-notice {
  margin: 0 0 8px;
  padding: 7px 11px;
  border-left: 2px solid rgba(255, 157, 46, 0.7);
  background: rgba(255, 157, 46, 0.09);
  color: #ffc879;
  font-size: 11.5px;
  line-height: 1.6;
}
/* 取消制作按钮：低对比幽灵按钮，不抢进度条的注意力 */
.studio-cancel {  align-self: flex-start;
  margin-top: 2px;
  padding: 4px 12px;
  border: 1px solid rgba(255, 157, 46, 0.45);
  background: transparent;
  color: #ffb45c;
  font-size: 11.5px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.18s;
}
.studio-cancel:hover:not(:disabled) {
  background: rgba(255, 157, 46, 0.14);
  color: #ffd2a8;
}
.studio-cancel:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.studio-done {
  color: #73f8d0;
}
.studio-err {
  color: #ffb45c;
  word-break: break-all;
}
/* 语音克隆环节（2026-11-11）：照片数字人绑定本人音色 */
.studio-voice {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px dashed rgba(255, 157, 46, 0.4);
  border-radius: 8px;
  background: rgba(255, 157, 46, 0.06);
}
.studio-voice-head {
  font-size: 13.5px;
  font-weight: 600;
  color: #ffc879;
  letter-spacing: 0.5px;
}
.studio-voice-hint {
  margin: 6px 0 8px;
  font-size: 12px;
  line-height: 1.55;
  color: rgba(214, 232, 244, 0.72);
}
.studio-voice-sentence {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(6, 22, 36, 0.55);
  font-size: 13px;
  line-height: 1.6;
  color: #d6e8f4;
}
.studio-voice-sentence .asset-btn {
  flex: none;
  margin-left: auto;
}
.studio-voice-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.studio-voice-bind {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  align-items: center;
}
.studio-voice-current {
  margin: 0 0 6px;
  font-size: 12px;
  color: rgba(214, 232, 244, 0.9);
}
.studio-voice-current b {
  color: #73f8d0;
  font-weight: 600;
}
.studio-voice-official {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.studio-voice-official-label {
  font-size: 12px;
  color: rgba(214, 232, 244, 0.72);
}
.studio-voice-official .asset-btn.active {
  border-color: #73f8d0;
  color: #73f8d0;
  background: rgba(115, 248, 208, 0.12);
}
.studio-voice-bind select {
  flex: 1;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid rgba(255, 157, 46, 0.45);
  border-radius: 6px;
  background: rgba(6, 22, 36, 0.8);
  color: #d6e8f4;
  font-size: 13px;
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
/* 对话面板：工具条固定，消息流弹性，输入区贴底 */
.capability-panel {
  display: flex;
  flex-direction: column;
  position: relative;
}
.capability-panel .dh-toolbar {
  flex: none;
}
/* 消息流限宽居中：宽屏下一行 60~70 字，长消息不会横跨整个面板 */
.chat-stream {
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
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
