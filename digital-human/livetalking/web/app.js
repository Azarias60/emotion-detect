const ui = {
  character: document.querySelector("#defaultCharacter"),
  characterImg: document.querySelector("#characterSource"),
  idle: document.querySelector("#defaultIdle"),
  talk: document.querySelector("#defaultTalk"),
  liveVideo: document.querySelector("#liveVideo"),
  statePill: document.querySelector("#statePill"),
  caption: document.querySelector("#caption"),
  defaultPanel: document.querySelector("#defaultPanel"),
  livePanel: document.querySelector("#livePanel"),
  enterLive: document.querySelector("#enterLive"),
  leaveLive: document.querySelector("#leaveLive"),
  retryLive: document.querySelector("#retryLive"),
  mic: document.querySelector("#micButton"),
  micLabel: document.querySelector("#micLabel"),
  defaultStatus: document.querySelector("#defaultStatus"),
  defaultConversation: document.querySelector("#defaultConversation"),
  defaultForm: document.querySelector("#defaultForm"),
  defaultInput: document.querySelector("#defaultInput"),
  personaRow: document.querySelector("#personaRow"),
  presetChips: document.querySelector("#presetChips"),
  presetMixHint: document.querySelector("#presetMixHint"),
  dropVideos: document.querySelector("#dropVideos"),
  dropGroup: document.querySelector("#dropGroup"),
  assetRefresh: document.querySelector("#assetRefresh"),
  danceGroup: document.querySelector("#danceGroup"),
  danceButton: document.querySelector("#danceButton"),
  danceLoading: document.querySelector("#danceLoading"),
  bootLoading: document.querySelector("#bootLoading"),
  bootLoadingText: document.querySelector("#bootLoadingText"),
  liveConversation: document.querySelector("#liveConversation"),
  liveForm: document.querySelector("#liveForm"),
  liveInput: document.querySelector("#liveInput"),
  liveMic: document.querySelector("#liveMicButton"),
  liveMicLabel: document.querySelector("#liveMicLabel"),
  interruptLive: document.querySelector("#interruptLive"),
  endLiveStream: document.querySelector("#endLiveStream"),
  connectionCard: document.querySelector(".connection-card"),
  connectionTitle: document.querySelector("#connectionTitle"),
  connectionDetail: document.querySelector("#connectionDetail"),
  steps: ["#stepAsr", "#stepLlm", "#stepTts"].map((id) => document.querySelector(id)),
};

const state = {
  mode: "default",
  config: {
    idleDelayMinMs: 3000,
    idleDelayMaxMs: 7000,
    vadThreshold: 0.035,
    vadSilenceMs: 900,
  },
  busy: false,
  listening: false,
  micStream: null,
  micSource: null,
  micNode: null,
  micSink: null,
  workletReady: false,
  audioContext: null,
  conversationSocket: null,
  socketPromise: null,
  nextAudioStart: 0,
  playbackSources: new Set(),
  assistantBubble: null,
  userBubble: null,
  responseTimer: null,
  idleTimer: null,
  pc: null,
  sessionId: null,
  connecting: false,
  connectGeneration: 0,
  // 人物与音色素材库（实时口型页）
  assets: null,
  avatar: window.localStorage.getItem("livetalking.avatar") || "",
  assetPollTimer: null,
  // 两套人设：default（轻量页，暖暖）/ live（实时口型，数字关怀伙伴）
  personas: { default: null, live: null },
  // 服务器预热（TTS 模型加载）未完成前不放行对话输入，避免第一句话排队
  backendReady: false,
  connKind: "connecting",
  // 心跳：让服务器知道还有页面开着（快捷方式启动时，页面全关服务器自动退出）
  serverEpoch: null,
  serverGone: false,
};

// The default-mode visual is a single canvas: the character image and every
// presented idle-video frame are drawn onto it. One paint surface means there
// is no DOM layer swap, so no black compositor frame can ever leak through.
// The backing store matches the on-screen size once and then never changes:
// resizing clears the texture and can present an empty frame, and pushing a
// full 1536x2720 texture every frame wasted GPU bandwidth for no visible gain.
const idleCtx = ui.character.getContext("2d");
const idleLoop = { token: 0, showingVideo: false };
// Short canvas-internal blend hiding the slight color/luma difference between
// the still image and the video grade. Drawn on one canvas, so it never
// touches the compositor layer state that caused the original black flashes.
const CROSSFADE_MS = 300;

function syncCanvasSize() {
  const rect = ui.character.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(2, Math.round(rect.width * dpr));
  const height = Math.max(2, Math.round(rect.height * dpr));
  if (ui.character.width !== width || ui.character.height !== height) {
    ui.character.width = width;
    ui.character.height = height;
    return true;
  }
  return false;
}

function paintCharacter(alpha = 1) {
  const img = ui.characterImg;
  if (!img.naturalWidth || !ui.character.width) return;
  const cw = ui.character.width;
  const ch = ui.character.height;
  const scale = Math.max(cw / img.naturalWidth, ch / img.naturalHeight);
  const w = img.naturalWidth * scale;
  const h = img.naturalHeight * scale;
  idleCtx.globalAlpha = alpha;
  idleCtx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
  idleCtx.globalAlpha = 1;
}

function paintVideoCover(alpha = 1, video = ui.idle) {
  const cw = ui.character.width;
  const ch = ui.character.height;
  if (!video.videoWidth || !cw) return;
  const scale = Math.max(cw / video.videoWidth, ch / video.videoHeight);
  const w = video.videoWidth * scale;
  const h = video.videoHeight * scale;
  idleCtx.globalAlpha = alpha;
  idleCtx.drawImage(video, (cw - w) / 2, (ch - h) / 2, w, h);
  idleCtx.globalAlpha = 1;
}

function runIdleLoop(token) {
  const startedAt = performance.now();
  const step = () => {
    if (token !== idleLoop.token) return;
    if (ui.idle.paused || ui.idle.ended || state.mode !== "default") {
      paintCharacter();
      return;
    }
    const t = Math.min(1, (performance.now() - startedAt) / CROSSFADE_MS);
    if (t < 1) {
      // Blend phase: image stays as the base, the video frame fades in on top.
      paintCharacter();
      paintVideoCover(t);
      requestAnimationFrame(step);
    } else {
      idleLoop.showingVideo = true;
      paintVideoCover();
      if (ui.idle.requestVideoFrameCallback) ui.idle.requestVideoFrameCallback(step);
      else requestAnimationFrame(step);
    }
  };
  if (ui.idle.requestVideoFrameCallback) ui.idle.requestVideoFrameCallback(step);
  else requestAnimationFrame(step);
}

function fadeVideoToImage(token) {
  const startedAt = performance.now();
  const step = () => {
    if (token !== idleLoop.token) return;
    const t = Math.min(1, (performance.now() - startedAt) / CROSSFADE_MS);
    // The ended/paused video keeps presenting its last frame, so it stays as
    // the base while the still image fades back in on top.
    paintVideoCover();
    paintCharacter(t);
    if (t < 1) requestAnimationFrame(step);
    else paintCharacter();
  };
  requestAnimationFrame(step);
}

function stopIdleVideo() {
  idleLoop.token += 1;
  if (!ui.idle.paused) ui.idle.pause();
  if (idleLoop.showingVideo && ui.idle.videoWidth) fadeVideoToImage(idleLoop.token);
  else paintCharacter();
  idleLoop.showingVideo = false;
}

// 说话视频状态机：开口说话时从待机切入并循环播放，直到语音排空；
// 片段切换遵守原子性——idle 视频播放中收到切换请求时先排队，播完
// 当前片段再切；静态图片期立即切换（图片本身没有播放长度）。
const talkVisual = { active: false, speaking: false, pending: null, token: 0 };

function startTalkLoop() {
  if (!ui.talk) return;
  const token = ++talkVisual.token;
  talkVisual.active = true;
  talkVisual.pending = null;
  try { ui.talk.currentTime = 0; } catch { /* seek not supported */ }
  // 入场淡入的底图：从 idle 末帧切入就用 idle 末帧，否则用静态立绘
  const fromIdleFrame = idleLoop.showingVideo && ui.idle.videoWidth;
  const paintBase = fromIdleFrame ? () => paintVideoCover() : () => paintCharacter();
  const startedAt = performance.now();
  const step = () => {
    if (token !== talkVisual.token) return;
    if (ui.talk.paused || ui.talk.ended) return;
    const t = Math.min(1, (performance.now() - startedAt) / CROSSFADE_MS);
    if (t < 1) {
      paintBase();
      paintVideoCover(t, ui.talk);
      requestAnimationFrame(step);
    } else {
      paintVideoCover(1, ui.talk);
      if (ui.talk.requestVideoFrameCallback) ui.talk.requestVideoFrameCallback(step);
      else requestAnimationFrame(step);
    }
  };
  ui.talk.play().then(() => {
    if (token !== talkVisual.token) return;
    if (ui.talk.requestVideoFrameCallback) ui.talk.requestVideoFrameCallback(step);
    else requestAnimationFrame(step);
  }).catch(() => stopTalkLoop());
}

function stopTalkLoop() {
  talkVisual.token += 1;
  talkVisual.active = false;
  talkVisual.speaking = false;
  talkVisual.pending = null;
  if (ui.talk && !ui.talk.paused) ui.talk.pause();
  paintCharacter();
}

if (ui.talk) {
  ui.talk.addEventListener("ended", () => {
    if (!talkVisual.active) return;
    if (talkVisual.speaking) {
      // 还没说完：无缝循环（首尾帧同图，接缝不可见）
      try { ui.talk.currentTime = 0; } catch { /* seek not supported */ }
      ui.talk.play().catch(() => stopTalkLoop());
    } else {
      // 语音已排空：本轮播完即回到待机循环
      talkVisual.token += 1;
      talkVisual.active = false;
      paintCharacter();
      scheduleIdle();
    }
  });
}

function requestTalkVisual() {
  if (!ui.talk || state.mode !== "default") return;
  talkVisual.speaking = true;
  if (talkVisual.active) return; // 已在循环中，继续即可
  if (!ui.idle.paused) {
    // idle 播放中：排队，等这个片段播完再切说话
    talkVisual.pending = "talk";
    return;
  }
  clearIdleTimer();
  idleLoop.token += 1;
  idleLoop.showingVideo = false;
  startTalkLoop();
}

function requestStandbyStill() {
  if (state.mode !== "default" || talkVisual.active) return;
  if (!ui.idle.paused) {
    talkVisual.pending = "listen"; // idle 播完再切静态图
    return;
  }
  stopIdleVideo();
}

function showVisual(element) {
  const showLive = element === ui.liveVideo;
  ui.character.classList.toggle("visible", !showLive);
  ui.liveVideo.classList.toggle("visible", showLive);
  stopTalkLoop();
  if (!showLive) stopIdleVideo();
}

function setDefaultStatus(label, detail) {
  ui.statePill.textContent = label;
  ui.defaultStatus.textContent = detail;
}

function setPipeline(count) {
  ui.steps.forEach((step, index) => step.classList.toggle("active", index < count));
}

function addMessage(container, role, text) {
  container.querySelector(".empty-message")?.remove();
  const bubble = document.createElement("div");
  bubble.className = `message ${role}`;
  bubble.textContent = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

function clearIdleTimer() {
  window.clearTimeout(state.idleTimer);
  state.idleTimer = null;
}

function scheduleIdle() {
  clearIdleTimer();
  if (state.mode !== "default" || state.busy || talkVisual.active) return;
  const min = state.config.idleDelayMinMs;
  const max = Math.max(min, state.config.idleDelayMaxMs);
  state.idleTimer = window.setTimeout(playIdle, min + Math.random() * (max - min));
}

function seekIdleStart() {
  // Wait until frame 0 is decoded (readyState >= HAVE_CURRENT_DATA) before
  // playback starts, so the first drawn frame is always real video content.
  const video = ui.idle;
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(guard);
      video.removeEventListener("seeked", finish);
      video.removeEventListener("loadeddata", finish);
      resolve();
    };
    const guard = window.setTimeout(finish, 1500);
    video.addEventListener("seeked", finish);
    video.addEventListener("loadeddata", finish);
    try { video.currentTime = 0; } catch { /* seek not supported */ }
    if (video.readyState >= 2 && video.currentTime === 0) finish();
  });
}

async function playIdle() {
  if (state.mode !== "default" || state.busy || talkVisual.active) return;
  await seekIdleStart();
  if (state.mode !== "default" || state.busy) return;
  const token = ++idleLoop.token;
  idleLoop.showingVideo = false;
  try {
    await ui.idle.play();
  } catch {
    if (token === idleLoop.token) {
      paintCharacter();
      scheduleIdle();
    }
    return;
  }
  if (token === idleLoop.token) runIdleLoop(token);
}

ui.idle.addEventListener("ended", () => {
  const wasShowingVideo = idleLoop.showingVideo;
  idleLoop.token += 1;
  idleLoop.showingVideo = false;
  if (talkVisual.pending === "talk") {
    // 播完 idle 片段，现在切入说话视频（从 idle 末帧淡入）
    talkVisual.pending = null;
    startTalkLoop();
    return;
  }
  if (wasShowingVideo && ui.idle.videoWidth) fadeVideoToImage(idleLoop.token);
  else paintCharacter();
  if (talkVisual.pending === "listen") {
    // 保持静态立绘等待（用户在说话/识别中），不排下一次 idle
    talkVisual.pending = null;
    return;
  }
  scheduleIdle();
});

function setBusy(value) {
  state.busy = value;
  ui.mic.classList.toggle("busy", value);
  ui.defaultInput.disabled = value;
  ui.defaultForm.querySelector("button").disabled = value;
  ui.enterLive.disabled = value;
  if (value) clearIdleTimer();
}

async function parseResponse(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.msg || `请求失败（${response.status}）`);
  return body;
}

async function sendDefaultText(text) {
  const socket = await ensureConversationSocket();
  await ensureAudioContext();
  setBusy(true);
  requestStandbyStill();
  setPipeline(2);
  setDefaultStatus("思考中", "LLM 正在组织回复");
  ui.caption.textContent = "让我想一想…";
  socket.send(JSON.stringify({ type: "configure", mode: "default" }));
  socket.send(JSON.stringify({ type: "text", text }));
}

const WORKLET_SOURCE = `
class LiveTalkingMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.output = new Int16Array(512);
    this.outputIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      const source = input[0];
      for (let i = 0; i < source.length; i++) {
        this.sum += source[i];
        this.count += 1;
        this.phase += 16000;
        if (this.phase < sampleRate) continue;
        this.phase -= sampleRate;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.output[this.outputIndex++] = value < 0 ? value * 32768 : value * 32767;
        this.sum = 0;
        this.count = 0;
        if (this.outputIndex === this.output.length) {
          const pcm = this.output;
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
          this.output = new Int16Array(512);
          this.outputIndex = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("livetalking-mic", LiveTalkingMic);
`;

async function ensureAudioContext() {
  if (!state.audioContext || state.audioContext.state === "closed") {
    state.audioContext = new AudioContext({ sampleRate: 16000 });
    state.nextAudioStart = 0;
    state.workletReady = false;
  }
  if (state.audioContext.state === "suspended") await state.audioContext.resume();
  return state.audioContext;
}

function sendMicConfiguration() {
  const socket = state.conversationSocket;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({
    type: "configure",
    mode: state.mode === "live" ? "live" : "default",
    sessionid: state.mode === "live" ? (state.sessionId || "") : "",
  }));
}

async function ensureConversationSocket() {
  if (state.conversationSocket?.readyState === WebSocket.OPEN) {
    return state.conversationSocket;
  }
  if (state.socketPromise) return state.socketPromise;
  state.socketPromise = new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws/conversation`);
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => {
      state.conversationSocket = socket;
      sendMicConfiguration();
      resolve(socket);
    }, { once: true });
    socket.addEventListener("message", handleConversationMessage);
    socket.addEventListener("close", () => {
      if (state.conversationSocket === socket) state.conversationSocket = null;
      state.socketPromise = null;
      if (state.listening) {
        disableMicrophone();
        if (state.mode === "default") {
          setDefaultStatus("连接断开", "实时麦克风连接已关闭");
        } else {
          ui.connectionDetail.textContent = "实时麦克风连接已关闭";
        }
      }
    });
    socket.addEventListener("error", () => reject(new Error("实时麦克风连接失败")), { once: true });
  }).finally(() => {
    state.socketPromise = null;
  });
  return state.socketPromise;
}

function flushStreamPlayback() {
  window.clearTimeout(state.responseTimer);
  state.playbackSources.forEach((source) => {
    try { source.stop(); } catch { /* already ended */ }
  });
  state.playbackSources.clear();
  if (state.audioContext) state.nextAudioStart = state.audioContext.currentTime;
  state.assistantBubble = null;
  // 用户开口打断：说话视频播完当前循环即回到待机
  talkVisual.speaking = false;
}

async function schedulePCM(arrayBuffer) {
  const context = await ensureAudioContext();
  const bytes = new Uint8Array(arrayBuffer);
  if (bytes.length <= 1 || bytes[0] !== 0x01) return;
  const pcmBytes = bytes.slice(1);
  const pcm = new Int16Array(pcmBytes.buffer);
  const buffer = context.createBuffer(1, pcm.length, 16000);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(context.destination);
  const start = Math.max(context.currentTime + 0.08, state.nextAudioStart);
  source.start(start);
  state.nextAudioStart = start + buffer.duration;
  state.playbackSources.add(source);
  source.addEventListener("ended", () => state.playbackSources.delete(source), { once: true });
}

function finishDefaultResponseWhenPlayed() {
  window.clearTimeout(state.responseTimer);
  const remaining = state.audioContext
    ? Math.max(0, state.nextAudioStart - state.audioContext.currentTime)
    : 0;
  state.responseTimer = window.setTimeout(() => {
    setBusy(false);
    setPipeline(0);
    setDefaultStatus(state.listening ? "聆听中" : "轻量待机", state.listening ? "请继续说话" : "麦克风尚未开启");
    ui.caption.textContent = state.listening ? "请说，我在听" : "点击麦克风，开始和我说话";
    // 语音已排空：说话视频播完当前循环后自动回到待机循环
    talkVisual.speaking = false;
    scheduleIdle();
  }, remaining * 1000 + 80);
}

const ACTION_LABELS = {
  greet: "动作 · 打招呼",
  encourage: "动作 · 鼓励",
  praise: "动作 · 表扬",
  comfort: "动作 · 安慰",
};

function micHintText() {
  const word = state.wakeWord || "你好";
  return state.wakeRequired ? `说「${word}」和我说话` : "请说，我在听";
}

function handleConversationEvent(event) {
  if (event.type === "configured") {
    state.wakeRequired = Boolean(event.wakeRequired);
    state.wakeWord = event.wakeWord || "";
    if (state.listening) ui.caption.textContent = micHintText();
  } else if (event.type === "wake_word_pending") {
    const word = state.wakeWord || "你好";
    if (state.mode === "default") {
      setBusy(false);
      setPipeline(0);
      setDefaultStatus("待唤醒", `先说「${word}」再开始对话`);
      ui.caption.textContent = `说「${word}」唤醒我`;
    } else {
      ui.connectionDetail.textContent = `说「${word}」唤醒数字人`;
    }
  } else if (event.type === "wake") {
    state.wakeRequired = false;
    if (state.mode === "default") {
      setDefaultStatus("已唤醒", "开始对话吧，我在听");
      ui.caption.textContent = "我在听，请说";
    } else {
      ui.connectionDetail.textContent = "已唤醒，请直接说";
    }
  } else if (event.type === "speech_started") {
    flushStreamPlayback();
    setBusy(true);
    clearIdleTimer();
    if (state.mode === "default") {
      requestStandbyStill();
      setPipeline(1);
      setDefaultStatus("正在听", "检测到人声，请继续说");
      ui.caption.textContent = "我在认真听…";
    } else {
      ui.connectionDetail.textContent = "正在听你说话…";
    }
  } else if (event.type === "speech_stopped") {
    if (state.mode === "default") {
      setDefaultStatus("识别中", "常驻 ASR 正在理解你的语音");
      ui.caption.textContent = "正在理解…";
    } else {
      ui.connectionDetail.textContent = "正在识别你的语音…";
    }
  } else if (event.type === "pipeline") {
    if (event.stage === "asr") setPipeline(1);
    if (event.stage === "llm") setPipeline(2);
  } else if (event.type === "transcript") {
    const container = event.mode === "live" ? ui.liveConversation : ui.defaultConversation;
    // The reopen grace can re-send a longer merged transcript for the same
    // turn; replace the pending user bubble instead of stacking a new one.
    if (state.userBubble && state.userBubble.isConnected && !state.assistantBubble) {
      state.userBubble.textContent = event.text;
    } else {
      state.userBubble = addMessage(container, "user", event.text);
    }
  } else if (event.type === "assistant_delta") {
    state.userBubble = null;
    if (!state.assistantBubble) {
      state.assistantBubble = addMessage(ui.defaultConversation, "assistant", "");
    }
    state.assistantBubble.textContent += event.text;
    ui.caption.textContent = state.assistantBubble.textContent;
    ui.defaultConversation.scrollTop = ui.defaultConversation.scrollHeight;
  } else if (event.type === "audio_start") {
    setPipeline(3);
    setDefaultStatus("正在说话", "语音正在流式播放");
    requestTalkVisual();
  } else if (event.type === "tts_fallback") {
    setDefaultStatus("已切换音源", "在线 TTS 暂不可用，本段改用本地 Windows 语音");
  } else if (event.type === "action") {
    const label = ACTION_LABELS[event.action] || `动作 · ${event.action}`;
    const container = state.mode === "live" ? ui.liveConversation : ui.defaultConversation;
    addMessage(container, "action", label);
    if (state.mode !== "live") ui.caption.textContent = label;
  } else if (event.type === "response_done") {
    state.assistantBubble = null;
    state.userBubble = null;
    finishDefaultResponseWhenPlayed();
  } else if (event.type === "response_accepted") {
    state.userBubble = null;
    setBusy(false);
    setPipeline(0);
    addMessage(ui.liveConversation, "assistant", "回复将由实时数字人播报");
    ui.connectionDetail.textContent = "正在生成实时语音和口型…";
  } else if (event.type === "error") {
    setBusy(false);
    setPipeline(0);
    state.userBubble = null;
    talkVisual.speaking = false; // 出错后说话循环播完当前轮即回待机
    if (state.mode === "default") {
      setDefaultStatus("处理失败", event.message);
      ui.caption.textContent = event.message;
      scheduleIdle();
    } else {
      ui.connectionDetail.textContent = event.message;
    }
  }
}

function handleConversationMessage(message) {
  if (typeof message.data === "string") {
    try { handleConversationEvent(JSON.parse(message.data)); } catch { /* ignore malformed event */ }
  } else {
    schedulePCM(message.data).catch(console.error);
  }
}

function updateMicrophoneButtons() {
  ui.mic.classList.toggle("listening", state.listening);
  ui.liveMic.classList.toggle("listening", state.listening);
  ui.mic.setAttribute("aria-pressed", String(state.listening));
  ui.liveMic.setAttribute("aria-pressed", String(state.listening));
  ui.micLabel.textContent = state.listening ? "麦克风已开启" : "开启麦克风";
  ui.liveMicLabel.textContent = state.listening ? "实时麦克风已开启" : "开启实时麦克风";
}

async function enableMicrophone() {
  if (state.listening) return;
  const socket = await ensureConversationSocket();
  const context = await ensureAudioContext();
  state.micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  if (!state.workletReady) {
    const workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
    try {
      await context.audioWorklet.addModule(workletUrl);
      state.workletReady = true;
    } finally {
      URL.revokeObjectURL(workletUrl);
    }
  }
  state.micSource = context.createMediaStreamSource(state.micStream);
  state.micNode = new AudioWorkletNode(context, "livetalking-mic");
  state.micNode.port.onmessage = (message) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(message.data);
  };
  state.micSource.connect(state.micNode);
  state.micSink = context.createGain();
  state.micSink.gain.value = 0;
  state.micNode.connect(state.micSink);
  state.micSink.connect(context.destination);
  state.listening = true;
  updateMicrophoneButtons();
  sendMicConfiguration();
  if (state.mode === "default") setDefaultStatus("聆听中", "实时传输中，停顿约 0.6 秒自动发送");
  else ui.connectionDetail.textContent = "实时麦克风已开启";
  ui.caption.textContent = micHintText();
}

function disableMicrophone() {
  if (!state.listening && !state.micStream) return;
  if (state.conversationSocket?.readyState === WebSocket.OPEN) {
    state.conversationSocket.send(JSON.stringify({ type: "cancel" }));
  }
  state.listening = false;
  state.micNode?.disconnect();
  state.micSource?.disconnect();
  state.micSink?.disconnect();
  state.micStream?.getTracks().forEach((track) => track.stop());
  state.micNode = null;
  state.micSource = null;
  state.micSink = null;
  state.micStream = null;
  updateMicrophoneButtons();
  if (state.mode === "default" && !state.busy) {
    setDefaultStatus("轻量待机", "麦克风尚未开启");
    ui.caption.textContent = "点击麦克风，开始和我说话";
  } else if (state.mode === "live") {
    ui.connectionDetail.textContent = "实时麦克风已关闭";
  }
}

function waitForIceGathering(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
  });
}

function waitForFirstVideoFrame(video, timeoutMs = 15000) {
  if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    let frameCallbackId = null;
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("实时画面首帧等待超时"));
    }, timeoutMs);
    const ready = () => {
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth) {
        cleanup();
        resolve();
      }
    };
    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener("loadeddata", ready);
      video.removeEventListener("playing", ready);
      if (frameCallbackId !== null && video.cancelVideoFrameCallback) {
        video.cancelVideoFrameCallback(frameCallbackId);
      }
    };
    video.addEventListener("loadeddata", ready);
    video.addEventListener("playing", ready);
    if (video.requestVideoFrameCallback) {
      frameCallbackId = video.requestVideoFrameCallback(() => {
        cleanup();
        resolve();
      });
    }
  });
}

function setConnection(kind, title, detail) {
  state.connKind = kind;
  ui.connectionCard.classList.toggle("connected", kind === "connected");
  ui.connectionCard.classList.toggle("failed", kind === "failed");
  ui.connectionCard.classList.toggle("stopped", kind === "stopped");
  ui.connectionTitle.textContent = title;
  ui.connectionDetail.textContent = detail;
  ui.retryLive.hidden = !["failed", "stopped"].includes(kind);
  // WebRTC 连上还不够：服务器语音模型没预热完，第一句话仍会排队
  const enabled = kind === "connected" && state.backendReady;
  ui.liveInput.disabled = !enabled;
  ui.liveForm.querySelector("button").disabled = !enabled;
  ui.interruptLive.disabled = !enabled;
  ui.endLiveStream.disabled = !enabled;
  ui.liveMic.disabled = !enabled;
}

async function connectLive() {
  if (state.connecting || state.pc) return false;
  const generation = ++state.connectGeneration;
  state.connecting = true;
  setConnection("connecting", "正在连接数字人", "正在准备模型与音视频轨道…");
  ui.caption.textContent = "正在准备实时画面，default 画面会保留到首帧就绪";
  let pc = null;
  try {
    pc = new RTCPeerConnection({ iceServers: [] });
    state.pc = pc;
    const remote = new MediaStream();
    ui.liveVideo.srcObject = remote;
    const videoTransceiver = pc.addTransceiver("video", { direction: "recvonly" });
    const audioTransceiver = pc.addTransceiver("audio", { direction: "recvonly" });
    [videoTransceiver.receiver, audioTransceiver.receiver].forEach((receiver) => {
      // Browser support differs, so use the available native jitter-buffer
      // control without making it a hard dependency.
      try {
        if ("playoutDelayHint" in receiver) receiver.playoutDelayHint = 0.12;
        if ("jitterBufferTarget" in receiver) receiver.jitterBufferTarget = 120;
      } catch (error) {
        console.debug("浏览器不支持手动设置 WebRTC 缓冲", error);
      }
    });
    pc.addEventListener("track", (event) => {
      remote.addTrack(event.track);
      ui.liveVideo.play().catch(console.warn);
    });
    pc.addEventListener("connectionstatechange", () => {
      if (pc !== state.pc) return;
      if (pc.connectionState === "connected") {
        setConnection("connecting", "音视频轨道已连接", "正在缓冲第一帧…");
      } else if (["failed", "disconnected"].includes(pc.connectionState)) {
        disableMicrophone();
        setConnection("failed", "连接已中断", "请点击重试重新建立 WebRTC");
      }
    });
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIceGathering(pc);
    // 显式取 sdp/type：RTCSessionDescription 的属性是原型 getter，
    // 展开运算符拿不到，会发出空对象导致 /offer 500
    const desc = pc.localDescription;
    const offerBody = { sdp: desc.sdp, type: desc.type };
    if (state.avatar) offerBody.avatar = state.avatar;
    const result = await parseResponse(await fetch("/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(offerBody),
    }));
    if (generation !== state.connectGeneration || state.mode !== "live") {
      pc.close();
      return false;
    }
    state.sessionId = result.sessionid;
    await pc.setRemoteDescription(result);
    await ui.liveVideo.play().catch(() => {});
    await waitForFirstVideoFrame(ui.liveVideo);
    if (generation !== state.connectGeneration || pc !== state.pc) {
      pc.close();
      return false;
    }
    showVisual(ui.liveVideo);
    setConnection("connected", "实时数字人已连接", "WebRTC 音视频同步播放中");
    sendMicConfiguration();
    updateDanceButton();
    ui.statePill.textContent = "实时口型";
    ui.caption.textContent = "实时画面已就绪，可以开始对话";
    return true;
  } catch (error) {
    console.error(error);
    pc?.close();
    if (pc === state.pc) {
      disableMicrophone();
      state.pc = null;
      state.sessionId = null;
      showVisual(ui.character);
      setConnection("failed", "实时模式连接失败", error.message);
      ui.caption.textContent = "连接失败，default 画面仍可正常显示";
    }
    return false;
  } finally {
    if (generation === state.connectGeneration) {
      state.connecting = false;
    }
  }
}

function disconnectLive() {
  state.connectGeneration += 1;
  state.connecting = false;
  state.pc?.close();
  state.pc = null;
  state.sessionId = null;
  ui.liveVideo.srcObject = null;
  updateDanceButton();
  setConnection("connecting", "正在连接数字人", "等待建立 WebRTC 会话");
}

async function enterLiveMode() {
  flushStreamPlayback();
  disableMicrophone();
  setBusy(false);
  setPipeline(0);
  clearIdleTimer();
  state.mode = "live";
  renderPersonas();
  loadAssets();
  ui.defaultPanel.classList.remove("active");
  ui.livePanel.classList.add("active");
  ui.statePill.textContent = "正在连接";
  ui.caption.textContent = "正在建立实时连接，首帧到达前继续显示 default";
  showVisual(ui.character);
  await connectLive();
}

function leaveLiveMode() {
  flushStreamPlayback();
  disableMicrophone();
  setBusy(false);
  setPipeline(0);
  disconnectLive();
  if (state.assetPollTimer) {
    window.clearInterval(state.assetPollTimer);
    state.assetPollTimer = null;
  }
  state.mode = "default";
  renderPersonas();
  sendMicConfiguration();
  ui.livePanel.classList.remove("active");
  ui.defaultPanel.classList.add("active");
  showVisual(ui.character);
  setDefaultStatus("轻量待机", "麦克风尚未开启");
  ui.caption.textContent = "点击麦克风，开始和我说话";
  scheduleIdle();
}

async function toggleMicrophone() {
  try {
    if (state.listening) disableMicrophone();
    else await enableMicrophone();
  } catch (error) {
    console.error(error);
    const name = error && error.name;
    const reason = name === "NotAllowedError"
      ? "浏览器拒绝了麦克风权限，请在地址栏的权限设置中允许麦克风"
      : name === "NotFoundError"
        ? "未找到可用的麦克风设备"
        : name === "NotReadableError"
          ? "麦克风被其他程序占用"
          : (error && error.message) || "未知错误";
    if (state.mode === "default") {
      setDefaultStatus("无法使用麦克风", `${reason}；页面需经 localhost 或 HTTPS 访问`);
    } else {
      ui.connectionDetail.textContent = `无法使用麦克风：${reason}`;
    }
  }
}

ui.mic.addEventListener("click", toggleMicrophone);
ui.liveMic.addEventListener("click", toggleMicrophone);
if (ui.assetRefresh) ui.assetRefresh.addEventListener("click", loadAssets);
if (ui.danceButton) ui.danceButton.addEventListener("click", startDance);

ui.defaultForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = ui.defaultInput.value.trim();
  if (!text || state.busy) return;
  ui.defaultInput.value = "";
  try {
    await sendDefaultText(text);
  } catch (error) {
    console.error(error);
    setBusy(false);
    setPipeline(0);
    setDefaultStatus("发送失败", error.message);
  }
});

ui.liveForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = ui.liveInput.value.trim();
  if (!text || !state.sessionId) return;
  ui.liveInput.value = "";
  addMessage(ui.liveConversation, "user", text);
  const pending = addMessage(ui.liveConversation, "assistant", "正在生成语音回复…");
  try {
    await parseResponse(await fetch("/human", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "chat", text, sessionid: state.sessionId }),
    }));
    pending.textContent = "回复将由实时数字人播报";
  } catch (error) {
    pending.textContent = error.message;
  }
});

ui.interruptLive.addEventListener("click", async () => {
  if (!state.sessionId) return;
  if (state.conversationSocket?.readyState === WebSocket.OPEN) {
    state.conversationSocket.send(JSON.stringify({ type: "cancel" }));
  }
  await fetch("/interrupt_talk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionid: state.sessionId }),
  });
});
ui.endLiveStream.addEventListener("click", async () => {
  flushStreamPlayback();
  disableMicrophone();
  setBusy(false);
  setPipeline(0);
  const sessionId = state.sessionId;
  if (sessionId) {
    await fetch("/interrupt_talk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionid: sessionId }),
    }).catch(console.warn);
  }
  disconnectLive();
  showVisual(ui.character);
  setConnection("stopped", "推流已结束", "WebRTC 会话和媒体轨道已释放");
  ui.statePill.textContent = "推流已结束";
  ui.caption.textContent = "实时推流已停止，可重新连接或返回轻量模式";
});
ui.enterLive.addEventListener("click", enterLiveMode);
ui.leaveLive.addEventListener("click", leaveLiveMode);
ui.retryLive.addEventListener("click", () => {
  disconnectLive();
  showVisual(ui.character);
  connectLive();
});
window.addEventListener("beforeunload", () => {
  disableMicrophone();
  state.conversationSocket?.close();
  disconnectLive();
});

function applyPersonaPayload(payload) {
  if (!payload) return;
  if (payload.default || payload.live) {
    if (payload.default) state.personas.default = payload.default;
    if (payload.live) state.personas.live = payload.live;
  } else if (payload.personas !== undefined) {
    state.personas[state.mode] = payload;
  }
  renderPersonas();
}

function renderPersonas() {
  const livePersona = document.querySelector("#livePersona");
  if (livePersona) {
    const live = state.personas.live;
    livePersona.textContent = `人设 · ${(live && (live.name || live.active)) || "--"}`;
  }
  if (!ui.personaRow) return;
  ui.personaRow.textContent = "";
  const summary = state.personas[state.mode];
  if (!summary) return;
  // 两个模式是两套人设，chips 只展示当前模式可用的那一套
  (summary.personas || []).forEach((persona) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "persona-chip" + (persona.active ? " active" : "");
    chip.textContent = persona.name;
    chip.title = persona.description || persona.name;
    chip.addEventListener("click", async () => {
      if (chip.classList.contains("active") || state.busy) return;
      try {
        const next = await parseResponse(await fetch("/api/persona", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: persona.id }),
        }));
        applyPersonaPayload(next);
        if (state.mode === "default") {
          const active = state.personas.default;
          setDefaultStatus("人设已切换", `${active.name} 已接管对话`);
        }
      } catch (error) {
        setDefaultStatus("切换失败", error.message);
      }
    });
    ui.personaRow.appendChild(chip);
  });
}

async function loadPersonas() {
  if (!ui.personaRow) return;
  try {
    const [defaultSummary, liveSummary] = await Promise.all([
      parseResponse(await fetch("/api/personas?mode=default")),
      parseResponse(await fetch("/api/personas?mode=live")),
    ]);
    state.personas.default = defaultSummary;
    state.personas.live = liveSummary;
    renderPersonas();
  } catch (error) {
    console.warn("人设列表加载失败", error);
  }
}

// ── 人物与音色素材库 ────────────────────────────────────────────────

async function loadAssets() {
  if (!ui.presetChips) return;
  try {
    state.assets = await parseResponse(await fetch("/api/assets"));
    renderAssets();
    scheduleAssetPolling();
  } catch (error) {
    console.warn("素材列表加载失败", error);
  }
}

function scheduleAssetPolling() {
  const running = (state.assets?.generations || []).some(
    (job) => job.status === "running"
  );
  if (running && !state.assetPollTimer) {
    state.assetPollTimer = window.setInterval(() => {
      // 页面已切走或没有任务了就停止轮询
      if (state.mode !== "live") {
        window.clearInterval(state.assetPollTimer);
        state.assetPollTimer = null;
        return;
      }
      loadAssets();
    }, 3000);
  } else if (!running && state.assetPollTimer) {
    window.clearInterval(state.assetPollTimer);
    state.assetPollTimer = null;
  }
}

function renderAssets() {
  const data = state.assets;
  if (!data) return;
  const presets = data.presets || [];
  // 记住的形象如果已经不在预设里（比如素材被删掉），悄悄回默认
  const presetAvatars = presets.map((item) => item.avatar);
  if (state.avatar && !presetAvatars.includes(state.avatar)) {
    state.avatar = "";
    window.localStorage.removeItem("livetalking.avatar");
  }
  const currentAvatar = state.avatar || data.defaultAvatar || "";
  // 只有 (形象, 音色) 完全命中的那项才点亮；混搭状态不点亮任何一项
  const activePreset = presets.find(
    (item) => item.avatar === currentAvatar && item.voice === data.activeVoice
  );

  ui.presetChips.textContent = "";
  presets.forEach((preset) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className =
      "persona-chip" + (preset.id === activePreset?.id ? " active" : "");
    const name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = preset.name || preset.id;
    chip.appendChild(name);
    if (preset.note) {
      const note = document.createElement("span");
      note.className = "chip-note";
      note.textContent = preset.note;
      chip.appendChild(note);
    }
    chip.title = preset.ready
      ? `形象 ${preset.avatar} + 音色 ${preset.voice}` +
        `\n动作：${(preset.actions || []).join("、") || "无"}`
      : `${preset.reason}，暂不可用`;
    if (!preset.ready) chip.disabled = true;
    chip.addEventListener("click", () => selectPreset(preset.id));
    ui.presetChips.appendChild(chip);
  });

  // 页面上一项就是一整对；如果当前形象/音色是从别处分别设的（混搭），
  // 没有哪一项能点亮，这时明说一句，别让人以为是坏了
  if (ui.presetMixHint) {
    if (activePreset || !presets.length) {
      ui.presetMixHint.hidden = true;
    } else {
      ui.presetMixHint.hidden = false;
      ui.presetMixHint.textContent =
        `当前是混搭（形象 ${currentAvatar} + 音色 ${data.activeVoice}），` +
        `点上面任意一项即可配成一对。`;
    }
  }

  const jobsByName = new Map(
    (data.generations || []).map((job) => [job.name, job])
  );
  ui.dropVideos.textContent = "";
  (data.dropVideos || []).forEach((item) => {
    if (item.built && !jobsByName.has(item.name)) return; // 已生成且无进行中任务
    const row = document.createElement("div");
    row.className = "drop-item";
    const name = document.createElement("span");
    name.className = "drop-name";
    name.textContent = `${item.file}${item.built ? "（已生成）" : ""}`;
    const job = jobsByName.get(item.name);
    if (job && job.status === "running") {
      const status = document.createElement("span");
      status.className = "drop-status";
      status.textContent = "生成中…";
      row.append(name, status);
    } else {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.built ? "重新生成" : "生成人物";
      btn.addEventListener("click", () => generateAvatar(item.name));
      const status = document.createElement("span");
      status.className = "drop-status";
      if (job && job.status === "error") status.textContent = job.message || "失败";
      row.append(name, status, btn);
    }
    ui.dropVideos.appendChild(row);
  });
  if (ui.dropGroup) ui.dropGroup.hidden = ui.dropVideos.children.length === 0;

  updateDanceButton();
}

// 舞蹈按钮：形象段表里有 dance 段才显示，且要等实时会话连上才能点
function updateDanceButton() {
  if (!ui.danceGroup || !ui.danceButton) return;
  const currentAvatar = state.avatar || state.assets?.defaultAvatar || "";
  const info = (state.assets?.avatars || []).find((item) => item.id === currentAvatar);
  const hasDance = Boolean(info?.dance);
  ui.danceGroup.hidden = !hasDance;
  ui.danceButton.disabled = !hasDance || !state.sessionId;
}

// 点"跳一段舞"：画布盖加载动画 → 服务器预加载舞蹈帧并切换画面
// → 接口返回即开始播放，播完自动走过渡段回到待机
async function startDance() {
  if (!state.sessionId) return;
  ui.danceButton.disabled = true;
  if (ui.danceLoading) ui.danceLoading.hidden = false;
  try {
    const response = await fetch("/dance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionid: state.sessionId }),
    });
    const body = await parseResponse(response);
    if (!response.ok || body.code !== 0) throw new Error(body.msg || "触发舞蹈失败");
  } catch (error) {
    console.warn("dance failed:", error);
    ui.caption.textContent = `舞蹈触发失败：${error.message || error}`;
  } finally {
    if (ui.danceLoading) ui.danceLoading.hidden = true;
    updateDanceButton();
  }
}

async function selectPreset(presetId) {
  const preset = (state.assets?.presets || []).find((item) => item.id === presetId);
  if (!preset || !preset.ready) return;
  const currentAvatar = state.avatar || state.assets?.defaultAvatar || "";
  const avatarChanged = preset.avatar !== currentAvatar;
  if (!avatarChanged && preset.voice === state.assets?.activeVoice) return;

  // 一项 = 形象 + 音色，两个一起换
  state.avatar = preset.avatar;
  window.localStorage.setItem("livetalking.avatar", preset.avatar);
  renderAssets();

  // 音色走 /api/voice（下一句生效）；形象要重建实时画面
  if (state.assets && preset.voice !== state.assets.activeVoice) {
    await selectVoice(preset.voice);
  }
  if (avatarChanged && state.mode === "live") {
    ui.connectionDetail.textContent = `正在切换到 ${preset.name}，重建实时画面…`;
    disconnectLive();
    await connectLive();
  }
}

async function selectVoice(voiceId) {
  if (!state.assets || voiceId === state.assets.activeVoice) return;
  try {
    const result = await parseResponse(await fetch("/api/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: voiceId }),
    }));
    state.assets.activeVoice = result.activeVoice || voiceId;
    if (state.assets.voices && result.voices) state.assets.voices = result.voices;
    renderAssets();
    ui.connectionDetail.textContent = result.message || `音色已切换为 ${voiceId}`;
  } catch (error) {
    console.error(error);
    ui.connectionDetail.textContent = `音色切换失败：${error.message}`;
  }
}

async function generateAvatar(name) {
  try {
    const result = await parseResponse(await fetch("/api/avatar/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }));
    ui.connectionDetail.textContent = result.message || `已开始生成 ${name}`;
    await loadAssets();
  } catch (error) {
    console.error(error);
    ui.connectionDetail.textContent = `生成失败：${error.message}`;
  }
}

function setBackendReady(ready) {
  if (state.backendReady === ready) return;
  state.backendReady = ready;
  ui.bootLoading.hidden = ready;
  // 轻量页的输入与麦克风跟随服务器就绪状态
  ui.defaultInput.disabled = !ready;
  ui.defaultForm.querySelector("button").disabled = !ready;
  ui.mic.disabled = !ready;
  if (ready) {
    // 实时页：WebRTC 若已连上，现在才真正放行输入
    if (state.connKind === "connected") {
      setConnection("connected", "实时数字人已连接", "WebRTC 音视频同步播放中");
    }
  } else {
    setDefaultStatus("模型加载中", "数字人模型加载中，完成后可开始对话…");
  }
}

async function pollBackendReadiness() {
  let failures = 0;
  while (!state.backendReady) {
    try {
      const response = await fetch("/api/readiness", { cache: "no-store" });
      const data = await response.json();
      if (data.ready) {
        if (data.tts === "failed") {
          setDefaultStatus("就绪（有告警）", "语音模型预热失败，对话可能偏慢");
        }
        setBackendReady(true);
        return;
      }
    } catch {
      failures += 1;
      // 探针持续异常（服务器重启中等）：放弃拦截，别把用户锁死在加载页
      if (failures >= 60) {
        setBackendReady(true);
        return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

// ── 服务器生命周期心跳 ──────────────────────────────────────────────
// 桌面快捷方式启动的服务器靠心跳判断"网页还开着"：这里每 5 秒报一次到，
// 关闭页面时用 sendBeacon 发告别。心跳响应里的 epoch 与页面记录的不一致
// 说明服务已被快捷方式重启过——本页面属于上一代服务，停止心跳（否则会把
// 新服务一直续命），告别信号也带上 epoch 让服务端忽略。
function startHeartbeat() {
  const beat = async () => {
    if (state.serverGone) return;
    try {
      const response = await fetch("/api/heartbeat", {
        method: "POST",
        cache: "no-store",
        keepalive: true,
      });
      const data = await response.json();
      if (state.serverEpoch === null) {
        state.serverEpoch = data.epoch;
      } else if (data.epoch !== state.serverEpoch) {
        state.serverGone = true;
        setDefaultStatus("服务已重启", "本页面已过期，请关闭后重新打开");
        return;
      }
    } catch {
      // 瞬时网络抖动忽略；长时间失败由服务端 idle 兜底
    }
  };
  beat();
  setInterval(beat, 5000);
  window.addEventListener("pagehide", () => {
    if (state.serverGone) return;
    const payload = state.serverEpoch === null
      ? ""
      : JSON.stringify({ epoch: state.serverEpoch });
    navigator.sendBeacon("/api/bye", payload);
  });
}

async function initialize() {
  console.log("[LiveTalking] visual v7: boot readiness gate");
  // 服务器语音模型可能在加载中（重启后约半分钟）：先用加载层拦住输入，
  // 就绪后再放行，保证第一句话不再排队等模型
  setDefaultStatus("模型加载中", "数字人模型加载中，完成后可开始对话…");
  ui.defaultInput.disabled = true;
  ui.defaultForm.querySelector("button").disabled = true;
  ui.mic.disabled = true;
  ui.bootLoading.hidden = false;
  pollBackendReadiness();
  startHeartbeat();
  showVisual(ui.character);
  syncCanvasSize();
  paintCharacter();
  const img = ui.characterImg;
  if (img.complete && img.naturalWidth) paintCharacter();
  else img.addEventListener("load", paintCharacter, { once: true });
  window.addEventListener("resize", () => {
    if (syncCanvasSize()) paintCharacter();
  });
  loadPersonas();
  loadAssets();
  try {
    const response = await fetch("/api/default/config");
    state.config = { ...state.config, ...(await response.json()) };
  } catch (error) {
    console.warn("使用默认前端配置", error);
  }
  // 管线第三芯片显示轻量页实际使用的 TTS 引擎
  if (state.config.defaultTts === "edge" && ui.steps[2]) {
    ui.steps[2].textContent = "Edge TTS";
  }
  scheduleIdle();
}

initialize();
