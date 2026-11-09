#include <Arduino.h>
#include <driver/i2s.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <esp_camera.h>
#include <LD6002.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

// ============================================================
// WiFi 配置
// ============================================================
const char* ssid = "HUAWEI_7FF0";
const char* password = "sjwei030530";

const uint16_t TCP_PORT = 8888;   // 温度 / 图像 / 心率
const uint16_t UDP_PORT = 8889;   // 音频

// ============================================================
// I2S 配置
// ============================================================
#define I2S_SCK         40
#define I2S_WS          42
#define I2S_SD          41

#define I2S_PORT        I2S_NUM_0
#define SAMPLE_RATE     48000

#define BITS_PER_SAMPLE I2S_BITS_PER_SAMPLE_32BIT
#define CHANNEL_FORMAT  I2S_CHANNEL_FMT_ONLY_LEFT

#define I2S_MODE        (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX)

#define DMA_BUF_COUNT   8
#define DMA_BUF_LEN     256


// ============================================================
// 数据包
// ============================================================

// ---------- 音频 ----------
struct AudioPacket {
    uint8_t tag;                    // 0x01
    int16_t samples[DMA_BUF_LEN];
} __attribute__((packed));

AudioPacket audioPkg;


// ---------- 温度 ----------
struct TempPacket {
    uint8_t tag;                    // 0x02
    float ambient;
    float object;
} __attribute__((packed));

TempPacket tempPkg;


// ---------- 雷达 ----------
struct RadarPacket {
    uint8_t tag;                    // 0x04
    float heartRate;
} __attribute__((packed));

RadarPacket radarPkg;


#include "TransportRuntime.h"

// ============================================================
// 传感器
// ============================================================

Adafruit_MLX90614 mlx = Adafruit_MLX90614();
LD6002 radar(Serial1);

bool cameraReady = false;


// ============================================================
// 摄像头配置
// ============================================================

camera_config_t config = {
    .pin_pwdn = -1,
    .pin_reset = -1,

    .pin_xclk = 18,

    .pin_sccb_sda = 2,
    .pin_sccb_scl = 1,

    .pin_d7 = 9,
    .pin_d6 = 10,
    .pin_d5 = 11,
    .pin_d4 = 12,
    .pin_d3 = 13,
    .pin_d2 = 14,
    .pin_d1 = 21,
    .pin_d0 = 47,

    .pin_vsync = 15,
    .pin_href = 16,
    .pin_pclk = 17,

    .xclk_freq_hz = 20000000,

    .pixel_format = PIXFORMAT_JPEG,

    .frame_size = FRAMESIZE_VGA,

    .jpeg_quality = 10,  // ESP32-CAMERA 数值越小画质越高；VGA 降低压缩损失

    .fb_count = 2,

    .fb_location = CAMERA_FB_IN_PSRAM,

    .grab_mode = CAMERA_GRAB_LATEST
};


// I2S 独立采集，避免摄像头抓帧阻塞音频；保留短读直到凑满 256 个采样。
void audioCaptureTask(void *) {
    int32_t samples[DMA_BUF_LEN];
    size_t filled = 0;
    for (;;) {
        size_t received = 0;
        const esp_err_t result = i2s_read(I2S_PORT, reinterpret_cast<uint8_t *>(samples) + filled,
                                        sizeof(samples) - filled, &received, pdMS_TO_TICKS(20));
        if (result != ESP_OK) { filled = 0; vTaskDelay(1); continue; }
        filled += received;
        if (filled == sizeof(samples)) {
            for (int i = 0; i < DMA_BUF_LEN; ++i) audioPkg.samples[i] = int16_t(samples[i] >> 16);
            sendAudioPacket(audioPkg);
            filled = 0;
        }
        if (!received) vTaskDelay(1);
    }
}

// ============================================================
// SETUP
// ============================================================

void setup() {

    Serial.begin(2000000);

    // 摄像头
    cameraReady =
        (esp_camera_init(&config) == ESP_OK);

    // I2S
    i2s_config_t i2sCfg = {
        .mode = I2S_MODE,
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = BITS_PER_SAMPLE,
        .channel_format = CHANNEL_FORMAT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = DMA_BUF_COUNT,
        .dma_buf_len = DMA_BUF_LEN,
        .use_apll = true,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pinCfg = {
        .bck_io_num = I2S_SCK,
        .ws_io_num = I2S_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = I2S_SD
    };

    i2s_driver_install(
        I2S_PORT,
        &i2sCfg,
        0,
        NULL
    );

    i2s_set_pin(
        I2S_PORT,
        &pinCfg
    );

    i2s_start(I2S_PORT);

    audioPkg.tag = 0x01;

    // MLX90614
    Wire.begin(2, 1);
    mlx.begin();
    tempPkg.tag = 0x02;

    // LD6002 雷达
    Serial1.begin(
        115200,
        SERIAL_8N1,
        44,
        43
    );

    radarPkg.tag = 0x04;
    radarPkg.heartRate = 0.0f;

    beginTransport(ssid, password, TCP_PORT, UDP_PORT);
    const BaseType_t audioResult = xTaskCreatePinnedToCore(audioCaptureTask, "AudioRx", 4096, nullptr, 2, nullptr, 1);
    configASSERT(audioResult == pdPASS);
}


// ============================================================
// LOOP
// ============================================================

void loop() {

    // ========================================================
    // 温度
    // ========================================================
    static unsigned long lastTempTime = 0;

    if (millis() - lastTempTime >= 500) {
        lastTempTime = millis();

        tempPkg.ambient =
            (float)mlx.readAmbientTempC();

        tempPkg.object =
            (float)mlx.readObjectTempC();

        sendSensorPacket(
            (uint8_t *)&tempPkg,
            sizeof(TempPacket)
        );
    }

    // ========================================================
    // 图像
    // ========================================================
    static unsigned long lastImgTime = 0;

    if (
        cameraReady &&
        millis() - lastImgTime >= 100
    ) {
        lastImgTime = millis();

        camera_fb_t *fb =
            esp_camera_fb_get();

        if (fb != NULL) {
            sendSensorPacket(
                fb->buf,
                fb->len
            );

            esp_camera_fb_return(fb);
        }
    }

    // ========================================================
    // 心率
    // ========================================================
    radar.update();

    if (radar.hasNewHeartRate()) {
        float hr = radar.getHeartRate();

        if (hr > 0) {
            radarPkg.heartRate = hr;
        }

        radar.clearHeartRateFlag();
    }

    static unsigned long lastRadarTime = 0;

    if (millis() - lastRadarTime >= 500) {
        lastRadarTime = millis();

        sendSensorPacket(
            (uint8_t *)&radarPkg,
            sizeof(RadarPacket)
        );
    }
    delay(1);
}
