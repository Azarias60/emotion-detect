<script setup>
import { useHardwareSettings } from '@/features/detection/useHardwareSettings'
const {
  isOpen,
  loading,
  message,
  statusData,
  selectedMode,
  wifiForm,
  serialForm,
  baudrateOptions,
  comPortSuggestions,
  badgeText,
  openModal,
  closeModal,
  handleApplySwitch,
  fetchStatus,
} = useHardwareSettings()
</script>

<template>
  <div class="mcu-switch-wrapper">
    <!-- 顶部状态栏触发按钮 -->
    <button
      type="button"
      class="mcu-badge-btn"
      :class="{ 'is-connected': statusData.isConnected }"
      title="点击切换单片机 WiFi / 串口 采集模式"
      @click="openModal"
    >
      <span class="pulse-indicator" :class="statusData.isConnected ? 'online' : 'offline'"></span>
      <span class="mcu-mode-icon">{{ statusData.transport?.type === 'wifi' ? '📶' : '⚡' }}</span>
      <span class="mcu-mode-text">{{ badgeText }}</span>
      <span class="mcu-state-tag">{{ statusData.isConnected ? '已连' : '未连' }}</span>
    </button>

    <!-- 模式切换科幻弹窗 -->
    <Teleport to="body">
      <div v-if="isOpen" class="modal-backdrop" @click.self="closeModal">
        <div class="modal-dialog">
          <!-- 弹窗科技感边角 -->
          <div class="corner corner-tl"></div>
          <div class="corner corner-tr"></div>
          <div class="corner corner-bl"></div>
          <div class="corner corner-br"></div>

          <!-- 头部 -->
          <div class="modal-header">
            <div class="modal-title">
              <span class="title-icon">⚙</span>
              <div>
                <h3>单片机采集模式设置</h3>
                <small>ESP32 MULTI-MODAL HARDWARE TRANSPORT CONFIG</small>
              </div>
            </div>
            <button type="button" class="close-btn" @click="closeModal">✕</button>
          </div>

          <!-- 实时连接状态横幅 -->
          <div class="status-banner" :class="statusData.isConnected ? 'online' : 'offline'">
            <div class="status-banner-left">
              <span class="banner-dot"></span>
              <div>
                <strong
                  >设备状态：{{
                    statusData.isConnected ? '已建立连接 (CONNECTED)' : '等待连接 (DISCONNECTED)'
                  }}</strong
                >
                <p>
                  当前底层：{{
                    statusData.transport?.type === 'wifi'
                      ? 'WiFi 无线双通道 (TCP+UDP)'
                      : '物理串口 (Serial)'
                  }}
                  <span v-if="statusData.transport?.type === 'wifi'">
                    ({{ statusData.transport.host }}:{{ statusData.transport.tcp_port }})
                  </span>
                  <span v-else>
                    ({{ statusData.transport?.port }} @ {{ statusData.transport?.baudrate }} bps)
                  </span>
                </p>
              </div>
            </div>
            <button
              type="button"
              class="refresh-btn"
              :disabled="loading"
              @click="fetchStatus(false)"
              title="立即探活"
            >
              ↻ 刷新
            </button>
          </div>

          <div v-if="statusData.transport?.type === 'wifi'" class="info-card">
            <div>
              <p v-if="statusData.transport.last_error">连接错误：{{ statusData.transport.last_error }}</p>
              <p v-else-if="statusData.isConnected && statusData.transport.tcp_bytes_received === 0">
                TCP 已连接，尚未收到设备数据。请核对设备 IP；使用 VPN/TUN 时请检查局域网直连设置。
              </p>
              <p v-if="statusData.transport.local_ip">本机连接地址：{{ statusData.transport.local_ip }}</p>
            </div>
          </div>

          <div v-if="statusData.isConnected" class="info-card">
            <p v-if="statusData.transport?.output_mode_status === 'confirmed'">
              设备已确认：仅输出 {{ statusData.transport.output_mode === 'wifi' ? 'WiFi' : 'USB 串口' }} 数据
            </p>
            <p v-else-if="statusData.transport?.output_mode_status === 'unconfirmed'">
              未收到模式确认，请确认已烧录支持通道切换的新固件。
            </p>
            <p v-else>正在等待设备确认输出模式…</p>
          </div>

          <!-- 模式选择 Tab -->
          <div class="mode-tabs">
            <div
              class="tab-item"
              :class="{ active: selectedMode === 'wifi' }"
              @click="selectedMode = 'wifi'"
            >
              <div class="tab-icon">📶</div>
              <div class="tab-info">
                <strong>WiFi 无线采集</strong>
                <small>TCP (图像+传感器) + UDP (实时音频)</small>
              </div>
            </div>
            <div
              class="tab-item"
              :class="{ active: selectedMode === 'serial' }"
              @click="selectedMode = 'serial'"
            >
              <div class="tab-icon">⚡</div>
              <div class="tab-info">
                <strong>USB 串口采集 (Serial)</strong>
                <small>ESP32-S3 USB 连接，COBS 帧流</small>
              </div>
            </div>
          </div>

          <!-- 表单区域 -->
          <div class="modal-body">
            <!-- WiFi 表单 -->
            <div v-if="selectedMode === 'wifi'" class="form-group-section">
              <div class="field-row">
                <label>ESP32 设备 IP 地址：</label>
                <div class="input-wrap">
                  <input
                    v-model="wifiForm.host"
                    type="text"
                    placeholder="如 192.168.8.11"
                    autocomplete="off"
                  />
                  <small class="tip">请填写 ESP32 当前 IP，确保后端主机与设备网络互通</small>
                </div>
              </div>

              <div class="field-grid">
                <div class="field-row">
                  <label>TCP 图像与传感器端口：</label>
                  <div class="input-wrap">
                    <input
                      v-model.number="wifiForm.tcp_port"
                      type="number"
                      placeholder="默认 8888"
                    />
                    <small class="tip">传输红外温度、心率与图像流</small>
                  </div>
                </div>

                <div class="field-row">
                  <label>UDP 音频监听端口：</label>
                  <div class="input-wrap">
                    <input
                      v-model.number="wifiForm.udp_port"
                      type="number"
                      placeholder="默认 8889"
                    />
                    <small class="tip">后端主机监听 48kHz PCM 音频</small>
                  </div>
                </div>
              </div>

              <div class="info-card">
                <span class="info-icon">💡</span>
                <div>
                  <strong>WiFi 采集架构说明 (对齐 mix.ino)</strong>
                  <p>
                    ESP32 启动后在指定 IP 开放 TCP 端口，PC 端发起 TCP 连接；同时 ESP32
                    将音频 UDP 单播至后端主机的 8889 端口。端口须与固件一致；选中后仅输出 WiFi 数据，USB 保留切换命令入口；检测按钮控制后端录制窗口。
                  </p>
                </div>
              </div>
            </div>

            <!-- 串口表单 -->
            <div v-else class="form-group-section">
              <div class="field-row">
                <label>串口端口号 (COM Port)：</label>
                <div class="input-wrap">
                  <div class="serial-port-input">
                    <input v-model="serialForm.port" type="text" placeholder="如 COM3" />
                    <div class="quick-coms">
                      <span
                        v-for="p in comPortSuggestions"
                        :key="p"
                        class="com-chip"
                        :class="{ selected: serialForm.port === p }"
                        @click="serialForm.port = p"
                      >
                        {{ p }}
                      </span>
                    </div>
                  </div>
                  <small class="tip">Windows 默认形如 COM3，Linux/Mac 形如 /dev/ttyUSB0</small>
                </div>
              </div>

              <div class="field-row">
                <label>串口波特率 (Baudrate)：</label>
                <div class="input-wrap">
                  <select v-model.number="serialForm.baudrate" class="tech-select">
                    <option v-for="b in baudrateOptions" :key="b" :value="b">
                      {{ b }} {{ b === 2000000 ? '(USB 默认)'  : '' }}
                    </option>
                  </select>
                  <small class="tip">ESP32-S3 USB 连接默认使用 2000000</small>
                </div>
              </div>

              <div class="info-card">
                <span class="info-icon">⚡</span>
                <div>
                  <strong>物理串口采集说明</strong>
                  <p>
                    选中后仅通过 USB 输出采集数据，WiFi 仍可接收切换命令。音视频与传感器数据经过 COBS 编码传输。
                  </p>
                </div>
              </div>
            </div>

            <!-- 提示信息横条 -->
            <div v-if="message.text" class="alert-bar" :class="message.type">
              {{ message.text }}
            </div>
          </div>

          <!-- 底部动作栏 -->
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="closeModal">取消</button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="loading"
              @click="handleApplySwitch"
            >
              <span v-if="loading" class="spinner"></span>
              <span>{{ loading ? '切换连接中...' : '立即应用并连接' }}</span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.mcu-switch-wrapper {
  display: inline-flex;
  align-items: center;
}

/* 顶部触发按钮 */
.mcu-badge-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 11px;
  border: 1px solid rgba(0, 242, 255, 0.35);
  background: rgba(0, 31, 48, 0.85);
  color: #83cbd2;
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.5px;
  transition: all 0.25s ease;
  clip-path: polygon(7px 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%, 0 7px);
}

.mcu-badge-btn:hover {
  color: #fff;
  border-color: #00f2ff;
  box-shadow:
    0 0 12px rgba(0, 242, 255, 0.25),
    inset 0 0 8px rgba(0, 242, 255, 0.15);
  transform: translateY(-1px);
}

.mcu-badge-btn.is-connected {
  border-color: rgba(0, 239, 188, 0.5);
}

.pulse-indicator {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transition: background 0.3s ease;
}

.pulse-indicator.online {
  background: #00efbc;
  box-shadow: 0 0 8px #00efbc;
  animation: pulse-glow 1.8s infinite;
}

.pulse-indicator.offline {
  /* 未连接是常态而非故障：用低饱和琥珀点，避免在青蓝主色里刺眼（评审反馈） */
  background: #b48a3a;
  box-shadow: 0 0 5px rgba(180, 138, 58, 0.7);
}

.mcu-mode-icon {
  font-size: 13px;
}

.mcu-mode-text {
  font-weight: 500;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcu-state-tag {
  font-size: 10px;
  padding: 1px 5px;
  background: rgba(0, 242, 255, 0.12);
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #00f2ff;
  /* 状态标签两个/三个字必须单行：曾因换行把"接"字挤出不可见（GLM 视觉核验 medium） */
  white-space: nowrap;
  flex: none;
  line-height: 1.5;
}

.is-connected .mcu-state-tag {
  background: rgba(0, 239, 188, 0.12);
  border-color: rgba(0, 239, 188, 0.4);
  color: #00efbc;
}

/* 弹窗遮罩 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 10, 20, 0.78);
  backdrop-filter: blur(6px);
}

/* 弹窗主体 */
.modal-dialog {
  position: relative;
  width: 90%;
  max-width: 580px;
  background: radial-gradient(circle at 50% 0%, rgba(0, 70, 100, 0.35), rgba(0, 18, 30, 0.98) 70%);
  border: 1px solid rgba(0, 242, 255, 0.4);
  box-shadow:
    0 0 35px rgba(0, 242, 255, 0.18),
    inset 0 0 25px rgba(0, 150, 200, 0.05);
  color: #ccecf0;
  font-family: 'Microsoft YaHei', Arial, sans-serif;
  padding: 24px 26px;
  clip-path: polygon(
    14px 0,
    100% 0,
    100% calc(100% - 14px),
    calc(100% - 14px) 100%,
    0 100%,
    0 14px
  );
  animation: modal-fade-in 0.25s ease-out;
}

/* 装饰角标 */
.corner {
  position: absolute;
  width: 8px;
  height: 8px;
  border-color: #00f2ff;
  pointer-events: none;
}
.corner-tl {
  top: 0;
  left: 0;
  border-top: 2px solid #00f2ff;
  border-left: 2px solid #00f2ff;
}
.corner-tr {
  top: 0;
  right: 0;
  border-top: 2px solid #00f2ff;
  border-right: 2px solid #00f2ff;
}
.corner-bl {
  bottom: 0;
  left: 0;
  border-bottom: 2px solid #00f2ff;
  border-left: 2px solid #00f2ff;
}
.corner-br {
  bottom: 0;
  right: 0;
  border-bottom: 2px solid #00f2ff;
  border-right: 2px solid #00f2ff;
}

/* 弹窗头部 */
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.2);
}

.modal-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  font-size: 22px;
  color: #00f2ff;
  animation: spin-slow 20s linear infinite;
}

.modal-title h3 {
  margin: 0;
  font-size: 17px;
  color: #00f2ff;
  letter-spacing: 1.5px;
}

.modal-title small {
  display: block;
  font-size: 9px;
  color: #5b8e99;
  letter-spacing: 1px;
}

.close-btn {
  background: transparent;
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #83cbd2;
  font-size: 15px;
  cursor: pointer;
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  transition: 0.2s;
}

.close-btn:hover {
  color: #fff;
  border-color: #ff5757;
  background: rgba(255, 87, 87, 0.2);
}

/* 状态横幅 */
.status-banner {
  margin: 16px 0;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 1px solid rgba(0, 242, 255, 0.2);
  background: rgba(0, 20, 32, 0.6);
}

.status-banner.online {
  border-color: rgba(0, 239, 188, 0.4);
  background: rgba(0, 239, 188, 0.08);
}

.status-banner.offline {
  border-color: rgba(255, 87, 87, 0.35);
  background: rgba(255, 87, 87, 0.08);
}

.status-banner-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.banner-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.online .banner-dot {
  background: #00efbc;
  box-shadow: 0 0 10px #00efbc;
}

.offline .banner-dot {
  background: #ff5757;
  box-shadow: 0 0 8px #ff5757;
}

.status-banner-left strong {
  font-size: 13px;
  color: #d4f8fa;
}

.status-banner-left p {
  margin: 3px 0 0;
  font-size: 11px;
  color: #72a5af;
}

.refresh-btn {
  background: rgba(0, 242, 255, 0.1);
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #00f2ff;
  padding: 5px 10px;
  font-size: 11px;
  cursor: pointer;
  transition: 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  background: rgba(0, 242, 255, 0.22);
}

/* 模式选择 Tab */
.mode-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 18px;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid rgba(0, 242, 255, 0.25);
  background: rgba(0, 24, 38, 0.65);
  cursor: pointer;
  transition: all 0.25s ease;
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
}

.tab-item:hover {
  border-color: rgba(0, 242, 255, 0.6);
  background: rgba(0, 35, 55, 0.85);
}

.tab-item.active {
  border-color: #00f2ff;
  background: rgba(0, 242, 255, 0.14);
  box-shadow:
    0 0 16px rgba(0, 242, 255, 0.18),
    inset 0 0 12px rgba(0, 242, 255, 0.1);
}

.tab-icon {
  font-size: 20px;
}

.tab-info strong {
  display: block;
  font-size: 13px;
  color: #e3f8fa;
}

.tab-item.active .tab-info strong {
  color: #00f2ff;
}

.tab-info small {
  display: block;
  font-size: 10px;
  color: #62909a;
  margin-top: 2px;
}

/* 表单字段 */
.field-row {
  margin-bottom: 14px;
}

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

label {
  display: block;
  font-size: 12px;
  color: #87c3cd;
  margin-bottom: 6px;
  letter-spacing: 0.5px;
}

.input-wrap input,
.tech-select {
  width: 100%;
  padding: 8px 11px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  background: rgba(0, 18, 30, 0.85);
  color: #00f2ff;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: all 0.2s;
  box-sizing: border-box;
}

.input-wrap input:focus,
.tech-select:focus {
  border-color: #00f2ff;
  box-shadow: 0 0 10px rgba(0, 242, 255, 0.35);
  background: rgba(0, 24, 40, 0.95);
}

.tip {
  display: block;
  font-size: 10px;
  color: #557d87;
  margin-top: 4px;
}

.serial-port-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.quick-coms {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.com-chip {
  padding: 3px 8px;
  font-size: 10px;
  border: 1px solid rgba(0, 242, 255, 0.25);
  background: rgba(0, 24, 38, 0.6);
  color: #83cbd2;
  cursor: pointer;
  transition: 0.2s;
}

.com-chip:hover {
  color: #fff;
  border-color: #00f2ff;
}

.com-chip.selected {
  border-color: #ff9d2e;
  color: #ff9d2e;
  background: rgba(255, 157, 46, 0.15);
}

/* 提示卡片 */
.info-card {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  margin-top: 8px;
  border: 1px dashed rgba(0, 242, 255, 0.22);
  background: rgba(0, 28, 44, 0.4);
  font-size: 11px;
}

.info-icon {
  font-size: 15px;
  color: #ff9d2e;
}

.info-card strong {
  color: #aae4eb;
}

.info-card p {
  margin: 3px 0 0;
  color: #63909a;
  line-height: 1.4;
}

/* 提示条 */
.alert-bar {
  margin-top: 14px;
  padding: 8px 12px;
  font-size: 12px;
  border-radius: 2px;
}

.alert-bar.info {
  background: rgba(0, 242, 255, 0.1);
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #00f2ff;
}

.alert-bar.success {
  background: rgba(0, 239, 188, 0.1);
  border: 1px solid rgba(0, 239, 188, 0.4);
  color: #00efbc;
}

.alert-bar.error {
  background: rgba(255, 87, 87, 0.12);
  border: 1px solid rgba(255, 87, 87, 0.4);
  color: #ff786f;
}

/* 弹窗底部 */
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid rgba(0, 242, 255, 0.18);
}

.btn {
  padding: 9px 18px;
  font-size: 12px;
  letter-spacing: 1px;
  cursor: pointer;
  transition: 0.2s;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.btn-secondary {
  border: 1px solid rgba(0, 242, 255, 0.3);
  background: rgba(0, 24, 38, 0.7);
  color: #83cbd2;
}

.btn-secondary:hover {
  color: #fff;
  border-color: #00f2ff;
}

.btn-primary {
  border: 1px solid #00f2ff;
  background: rgba(0, 242, 255, 0.18);
  color: #00f2ff;
  font-weight: bold;
}

.btn-primary:hover:not(:disabled) {
  background: rgba(0, 242, 255, 0.32);
  box-shadow: 0 0 14px rgba(0, 242, 255, 0.35);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(0, 242, 255, 0.3);
  border-top-color: #00f2ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes spin-slow {
  to {
    transform: rotate(360deg);
  }
}

@keyframes pulse-glow {
  0% {
    transform: scale(0.95);
    opacity: 0.8;
  }
  50% {
    transform: scale(1.2);
    opacity: 1;
    box-shadow: 0 0 12px #00efbc;
  }
  100% {
    transform: scale(0.95);
    opacity: 0.8;
  }
}

@keyframes modal-fade-in {
  from {
    opacity: 0;
    transform: scale(0.95) translateY(-10px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}
</style>
