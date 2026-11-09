#pragma once
#include <atomic>
#include <errno.h>
#include <lwip/sockets.h>

// 数据输出互斥，控制入口常驻。只有各自发送任务操作 USB/TCP 输出。
// MODE WIFI\n / MODE SERIAL\n -> COBS {0x10, version=1, mode=1|2} + 00
enum class OutputMode : uint8_t { Serial = 1, Wifi = 2 };
static std::atomic<uint32_t> outputState{1}; // 高位：切换代次，低字节：模式
static std::atomic<bool> wifiPeerReady{false};
static QueueHandle_t serialPackets, serialImages, tcpPackets, tcpImages, udpPackets;
static const char *networkSsid, *networkPassword;
static uint16_t dataTcpPort, dataUdpPort;

struct TxPacket {
    uint8_t *data = nullptr;
    size_t length = 0;
    uint32_t state = 0;
};
struct UdpPacket { AudioPacket audio; uint32_t state; };

static uint32_t selectOutput(OutputMode mode) {
    uint32_t previous = outputState.load();
    uint32_t next;
    do { next = ((previous & ~255u) + 256u) | uint8_t(mode); }
    while (!outputState.compare_exchange_weak(previous, next));
    return next;
}

// 标准 COBS，包括尾零对应的空块。
static TxPacket encodePacket(const uint8_t *data, size_t length, uint32_t state) {
    TxPacket result;
    result.data = static_cast<uint8_t *>(malloc(length + length / 254 + 3));
    if (!result.data) return result;
    size_t write = 1, codeIndex = 0;
    uint8_t code = 1;
    for (size_t i = 0; i < length; ++i) {
        if (data[i] == 0) {
            result.data[codeIndex] = code;
            codeIndex = write++;
            code = 1;
        } else {
            result.data[write++] = data[i];
            if (++code == 255) {
                result.data[codeIndex] = code;
                codeIndex = write++;
                code = 1;
            }
        }
    }
    result.data[codeIndex] = code;
    result.data[write++] = 0;
    result.length = write;
    result.state = state;
    return result;
}

static void releasePacket(TxPacket &packet) {
    free(packet.data);
    packet = TxPacket{};
}

static void queuePacket(QueueHandle_t queue, TxPacket packet, bool latestOnly) {
    if (!packet.data) return;
    if (xQueueSend(queue, &packet, 0) == pdTRUE) return;
    if (latestOnly) {
        TxPacket old;
        if (xQueueReceive(queue, &old, 0) == pdTRUE) releasePacket(old);
        if (xQueueSend(queue, &packet, 0) == pdTRUE) return;
    }
    releasePacket(packet);
}

static void sendSensorPacket(const uint8_t *data, size_t length) {
    const uint32_t state = outputState.load();
    const bool wifi = uint8_t(state) == uint8_t(OutputMode::Wifi);
    if (wifi && !wifiPeerReady.load()) return;
    const bool image = length > 1 && data[0] == 0xff && data[1] == 0xd8;
    QueueHandle_t queue = wifi ? (image ? tcpImages : tcpPackets)
                              : (image ? serialImages : serialPackets);
    queuePacket(queue, encodePacket(data, length, state), image);
}

static void sendAudioPacket(const AudioPacket &audio) {
    const uint32_t state = outputState.load();
    if (uint8_t(state) == uint8_t(OutputMode::Serial)) {
        queuePacket(serialPackets, encodePacket(reinterpret_cast<const uint8_t *>(&audio), sizeof(audio), state), false);
    } else if (wifiPeerReady.load()) {
        UdpPacket packet{audio, state};
        if (xQueueSend(udpPackets, &packet, 0) != pdTRUE) {
            UdpPacket old;
            xQueueReceive(udpPackets, &old, 0);
            xQueueSend(udpPackets, &packet, 0);
        }
    }
}

// 独立的有界命令缓冲；无动态 String，超长行丢弃至换行。
struct ModeCommand {
    char text[24]{};
    size_t size = 0;
    bool overflow = false;
    bool feed(char ch, OutputMode &mode) {
        if (ch == '\r') return false;
        if (ch != '\n') {
            if (size < sizeof(text) - 1) text[size++] = ch;
            else overflow = true;
            return false;
        }
        text[size] = 0;
        const bool wifi = !overflow && strcmp(text, "MODE WIFI") == 0;
        const bool serial = !overflow && strcmp(text, "MODE SERIAL") == 0;
        size = 0; overflow = false;
        if (wifi || serial) { mode = wifi ? OutputMode::Wifi : OutputMode::Serial; return true; }
        return false;
    }
};

struct OutputWriter {
    TxPacket packet;
    size_t offset = 0;
    uint32_t state = 0;
    bool delimiter = true;
    uint8_t ack[5]{};
    size_t ackOffset = 5;
    uint32_t progressAt = 0;

    void reset() {
        releasePacket(packet); offset = 0; delimiter = true;
        ackOffset = 5; progressAt = millis();
    }
    void confirm(OutputMode mode) {
        reset();
        state = selectOutput(mode);
        ack[0] = 4; ack[1] = 0x10; ack[2] = 1; ack[3] = uint8_t(mode); ack[4] = 0;
        ackOffset = 0;
    }
    template <typename Send> void pump(QueueHandle_t packets, QueueHandle_t images, OutputMode lane, Send send) {
        const uint32_t current = outputState.load();
        if (state != current) { reset(); state = current; }
        // 代次变化时用分隔符结束旧半帧，然后发送确认；未选中通道不发采集数据。
        if (delimiter) {
            const uint8_t zero = 0;
            if (send(&zero, 1) == 1) { delimiter = false; progressAt = millis(); }
            return;
        }
        if (ackOffset < sizeof(ack)) {
            ackOffset += send(ack + ackOffset, sizeof(ack) - ackOffset);
            return;
        }
        if (!packet.data) {
            if (xQueueReceive(packets, &packet, 0) != pdTRUE) xQueueReceive(images, &packet, 0);
            offset = 0; progressAt = millis();
        }
        if (!packet.data) return;
        if (packet.state != current || uint8_t(current) != uint8_t(lane)) { releasePacket(packet); return; }
        const size_t remaining = packet.length - offset;
        const size_t count = send(packet.data + offset, remaining > 1460 ? 1460 : remaining);
        offset += count;
        if (count) progressAt = millis();
        if (offset == packet.length) releasePacket(packet);
    }
};

static void serialTask(void *) {
    ModeCommand command;
    OutputWriter writer;
    for (;;) {
        OutputMode mode;
        for (int i = 0; i < 64 && Serial.available(); ++i)
            if (command.feed(char(Serial.read()), mode)) writer.confirm(mode);
        for (int burst = 0; burst < 8; ++burst) writer.pump(serialPackets, serialImages, OutputMode::Serial, [](const uint8_t *data, size_t size) -> size_t {
            const int available = Serial.availableForWrite();
            if (available <= 0) return 0;
            return Serial.write(data, size < size_t(available) ? size : size_t(available));
        });
        // 未读 USB 不允许积压完整旧图；下次从分隔符恢复。
        if (writer.packet.data && millis() - writer.progressAt > 500) writer.reset();
        vTaskDelay(1);
    }
}

static void wifiTask(void *) {
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false); WiFi.setAutoReconnect(true); WiFi.setSleep(false);
    WiFi.begin(networkSsid, networkPassword);
    WiFiServer server(dataTcpPort);
    WiFiClient peer;
    WiFiUDP udp;
    bool wasConnected = false;
    uint32_t lastReconnect = 0;
    ModeCommand command;
    OutputWriter writer;
    for (;;) {
        const bool connected = WiFi.status() == WL_CONNECTED;
        if (connected && !wasConnected) server.begin();
        if (!connected) {
            peer.stop(); wifiPeerReady = false;
            if (millis() - lastReconnect > 5000) { lastReconnect = millis(); WiFi.reconnect(); }
        }
        wasConnected = connected;
        if (connected && server.hasClient()) {
            WiFiClient incoming = server.available();
            if (!peer || !peer.connected()) {
                peer.stop(); peer = incoming; peer.setNoDelay(true);
                command = ModeCommand{}; writer.reset();
                // 新连接丢弃旧连接排队的数据，但不自动改变输出模式。
                selectOutput(static_cast<OutputMode>(uint8_t(outputState.load())));
            } else incoming.stop();
        }
        const bool ready = connected && peer && peer.connected();
        wifiPeerReady = ready;
        if (ready) {
            OutputMode mode;
            for (int i = 0; i < 64 && peer.available(); ++i)
                if (command.feed(char(peer.read()), mode)) writer.confirm(mode);
        }
        // UDP 先批量排空；TCP 使用非阻塞小块，不能拖住音频和模式命令。
        UdpPacket audio;
        for (int i = 0; i < 16 && xQueueReceive(udpPackets, &audio, 0) == pdTRUE; ++i) {
            if (ready && audio.state == outputState.load() && uint8_t(audio.state) == uint8_t(OutputMode::Wifi)) {
                udp.beginPacket(peer.remoteIP(), dataUdpPort);
                udp.write(reinterpret_cast<const uint8_t *>(&audio.audio), sizeof(audio.audio));
                udp.endPacket();
            }
        }
        writer.pump(tcpPackets, tcpImages, OutputMode::Wifi, [&](const uint8_t *data, size_t size) -> size_t {
            if (!ready) return 0;
            const int sent = ::send(peer.fd(), data, size, MSG_DONTWAIT);
            if (sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK) peer.stop();
            return sent > 0 ? size_t(sent) : 0;
        });
        if (!ready) writer.reset();
        else if (writer.packet.data && millis() - writer.progressAt > 500) {
            peer.stop(); writer.reset(); wifiPeerReady = false;
        }
        vTaskDelay(1);
    }
}

static void beginTransport(const char *ssid, const char *password, uint16_t tcpPort, uint16_t udpPort) {
    networkSsid = ssid; networkPassword = password; dataTcpPort = tcpPort; dataUdpPort = udpPort;
    serialPackets = xQueueCreate(32, sizeof(TxPacket)); serialImages = xQueueCreate(1, sizeof(TxPacket));
    tcpPackets = xQueueCreate(8, sizeof(TxPacket)); tcpImages = xQueueCreate(1, sizeof(TxPacket));
    udpPackets = xQueueCreate(16, sizeof(UdpPacket));
    configASSERT(serialPackets && serialImages && tcpPackets && tcpImages && udpPackets);
    const BaseType_t serialResult = xTaskCreatePinnedToCore(serialTask, "SerialTx", 4096, nullptr, 2, nullptr, 0);
    const BaseType_t wifiResult = xTaskCreatePinnedToCore(wifiTask, "WiFiTx", 8192, nullptr, 2, nullptr, 0);
    configASSERT(serialResult == pdPASS && wifiResult == pdPASS);
}
