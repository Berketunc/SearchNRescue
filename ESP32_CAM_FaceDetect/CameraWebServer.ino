#if 0
#include "esp_camera.h"
#include <WiFi.h>

// Keep this header from CameraWebServer example.
// Make sure only ONE model is enabled inside it:
// #define CAMERA_MODEL_AI_THINKER
#include "board_config.h"

// AP mode (no router needed)
const char *apSsid = "ESP32-CAM";
const char *apPass = "12345678";  // minimum 8 chars

// These come from the CameraWebServer example tab (app_httpd.cpp)
void startCameraServer();
void setupLedFlash();

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Good defaults for stable stream + face features
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;

  if (psramFound()) {
    config.fb_count = 2;
    config.jpeg_quality = 10;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.frame_size = FRAMESIZE_QVGA;
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    while (true) {
      delay(1000);
    }
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s && s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  if (s) {
    s->set_framesize(s, FRAMESIZE_QVGA);
  }

#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
  if (s) {
    s->set_vflip(s, 1);
    s->set_hmirror(s, 1);
  }
#endif

#if defined(CAMERA_MODEL_ESP32S3_EYE)
  if (s) {
    s->set_vflip(s, 1);
  }
#endif

#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  // AP start
  WiFi.mode(WIFI_AP);
  bool apOk = WiFi.softAP(apSsid, apPass);
  if (!apOk) {
    Serial.println("softAP start failed");
    while (true) {
      delay(1000);
    }
  }

  IPAddress ip = WiFi.softAPIP();
  Serial.print("AP SSID: ");
  Serial.println(apSsid);
  Serial.print("AP IP: ");
  Serial.println(ip);

  // Starts web UI + stream + face detect/recognition controls
  startCameraServer();

  Serial.print("Open: http://");
  Serial.println(ip);
  Serial.println("In UI: Start Stream -> enable Face Detection -> enable Face Recognition");
  Serial.print("Human monitor: http://");
  Serial.print(ip);
  Serial.println("/human");
}

void loop() {
  delay(10000);
}
#endif