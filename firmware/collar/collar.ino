// PawPatrol collar firmware: Glyph ESP32-C6 + MPU6050 + electret mic (+ RGB LED, buttons)
// Samples at 50 Hz and streams CSV lines over Wi-Fi UDP, BATCH lines per packet (one line per sample):
//   seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,loud
//   accel in g, gyro in deg/s, mic = peak-to-peak ADC counts over the last 20 ms (0 if unused),
//   buttons 1 = pressed, loud = 1 if the mic is well above its background level (onboard LED flashes)
// Listens for LED commands on CMD_PORT: "CALM", "ATTN", "ALERT", "OFF" or "LED r g b" (0-255)

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

// ---------- EDIT THESE ----------
const char* WIFI_SSID = "YOUR_HOTSPOT_NAME";   // 2.4 GHz only
const char* WIFI_PASS = "YOUR_HOTSPOT_PASSWORD";
const char* LAPTOP_IP = "192.168.0.100";       // laptop IP on the same hotspot
const bool  LED_COMMON_ANODE = true;           // our LED: long leg to 3.3V. false if long leg goes to GND
const bool  USE_MIC = true;                    // false if the mic is not wired
// --------------------------------

const uint16_t DATA_PORT = 4210;  // ESP32 -> laptop
const uint16_t CMD_PORT  = 4211;  // laptop -> ESP32

// Glyph C6 pins (avoid GPIO8/9 = boot strapping, GPIO12/13 = USB)
const int SDA_PIN   = 4;   // board label SDA
const int SCL_PIN   = 5;   // board label SCL
const int MIC_PIN   = 2;   // A2 (analog)
const int BTN_A_PIN = 6;   // D6, button to GND: "event marker"
const int BTN_B_PIN = 3;   // A3, button to GND: spare
const int LED_R_PIN = 18;  // D18
const int LED_G_PIN = 19;  // D19
const int LED_B_PIN = 20;  // D20
const int BOARD_LED = 14;  // onboard LED

const uint8_t MPU_ADDR = 0x68;           // 0x69 if AD0 is tied high
const float ACCEL_LSB_PER_G   = 4096.0;  // +-8 g range
const float GYRO_LSB_PER_DPS  = 32.8;    // +-1000 deg/s range
const uint32_t SAMPLE_US = 20000;        // 50 Hz
const int BATCH = 5;                     // samples per UDP packet (10 packets/s): copes with busy Wi-Fi
const int LED_MAX = 80;                  // cap brightness (protects LED if no resistors)
const float LOUD_FACTOR = 3.0;           // loud = mic above 3x background...
const int   LOUD_MIN_COUNTS = 40;        // ...and at least this many ADC counts above it

WiFiUDP udp;       // outgoing data
WiFiUDP cmdUdp;    // incoming LED commands
IPAddress laptop;
uint32_t seq = 0;
uint32_t nextSampleUs = 0;
bool mpuOk = false;
char batchBuf[BATCH * 128];
int batchLen = 0, batchCount = 0;
int micLo = 4095, micHi = 0;   // min/max since the last packet
float micFloor = -1;           // slowly-adapting background level
uint32_t loudUntilMs = 0;

void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR);
  if (Wire.endTransmission() != 0) return false;
  mpuWrite(0x6B, 0x00);  // wake up
  mpuWrite(0x1A, 0x03);  // low-pass filter ~44 Hz
  mpuWrite(0x1B, 0x10);  // gyro +-1000 deg/s
  mpuWrite(0x1C, 0x10);  // accel +-8 g (galloping can exceed 4 g)
  return true;
}

bool mpuRead(float a[3], float g[3]) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)14) != 14) return false;
  int16_t v[7];
  for (int i = 0; i < 7; i++) v[i] = (Wire.read() << 8) | Wire.read();
  for (int i = 0; i < 3; i++) a[i] = v[i] / ACCEL_LSB_PER_G;
  for (int i = 0; i < 3; i++) g[i] = v[i + 4] / GYRO_LSB_PER_DPS;  // v[3] is temperature
  return true;
}

// Called as often as possible between IMU samples so short sounds aren't missed
void micSample() {
  if (!USE_MIC) return;
  int s = analogRead(MIC_PIN);
  if (s < micLo) micLo = s;
  if (s > micHi) micHi = s;
}

// Peak-to-peak since the last call + loud flag; flashes the onboard LED on loud sounds
int micTakeLevel(bool &loud) {
  loud = false;
  if (!USE_MIC || micHi < micLo) return 0;
  int p2p = micHi - micLo;
  micLo = 4095;
  micHi = 0;
  if (micFloor < 0) micFloor = p2p;
  loud = p2p > micFloor * LOUD_FACTOR && p2p > micFloor + LOUD_MIN_COUNTS;
  if (!loud) micFloor = 0.98 * micFloor + 0.02 * p2p;  // only learn background from quiet moments
  if (loud) loudUntilMs = millis() + 150;
  digitalWrite(BOARD_LED, millis() < loudUntilMs ? HIGH : LOW);
  return p2p;
}

void setLed(int r, int g, int b) {
  int vals[3] = {r, g, b};
  int pins[3] = {LED_R_PIN, LED_G_PIN, LED_B_PIN};
  for (int i = 0; i < 3; i++) {
    int duty = constrain(vals[i], 0, 255) * LED_MAX / 255;
    analogWrite(pins[i], LED_COMMON_ANODE ? 255 - duty : duty);
  }
}

void handleCommands() {
  int n = cmdUdp.parsePacket();
  if (n <= 0) return;
  char buf[64];
  int len = cmdUdp.read(buf, sizeof(buf) - 1);
  buf[len > 0 ? len : 0] = 0;
  String cmd = String(buf);
  cmd.trim();
  cmd.toUpperCase();
  if (cmd == "CALM") setLed(0, 255, 0);
  else if (cmd == "ATTN") setLed(255, 120, 0);
  else if (cmd == "ALERT") setLed(255, 0, 0);
  else if (cmd == "OFF") setLed(0, 0, 0);
  else if (cmd.startsWith("LED")) {
    int r = 0, g = 0, b = 0;
    sscanf(buf + 3, "%d %d %d", &r, &g, &b);
    setLed(r, g, b);
  }
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    digitalWrite(BOARD_LED, !digitalRead(BOARD_LED));
    setLed(0, 0, 255);  // blue = connecting
    delay(250);
    Serial.print(".");
  }
  digitalWrite(BOARD_LED, LOW);  // off; flashes on loud sounds
  Serial.printf("\nConnected. ESP32 IP: %s\n", WiFi.localIP().toString().c_str());
  setLed(0, 255, 0);
}

void setup() {
  Serial.begin(115200);
  pinMode(BOARD_LED, OUTPUT);
  pinMode(BTN_A_PIN, INPUT_PULLUP);
  pinMode(BTN_B_PIN, INPUT_PULLUP);
  pinMode(LED_R_PIN, OUTPUT);
  pinMode(LED_G_PIN, OUTPUT);
  pinMode(LED_B_PIN, OUTPUT);
  setLed(0, 0, 0);

  Wire.begin(SDA_PIN, SCL_PIN, 400000);
  mpuOk = mpuInit();
  Serial.println(mpuOk ? "MPU6050 found" : "MPU6050 NOT found - check wiring (SDA=4, SCL=5, 3.3V, GND)");

  laptop.fromString(LAPTOP_IP);
  connectWifi();
  cmdUdp.begin(CMD_PORT);
  nextSampleUs = micros();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  handleCommands();
  micSample();

  if ((int32_t)(micros() - nextSampleUs) < 0) return;
  nextSampleUs += SAMPLE_US;
  if ((int32_t)(micros() - nextSampleUs) > 100000) nextSampleUs = micros();  // >100 ms behind: skip ahead, don't burst

  float a[3] = {0, 0, 0}, g[3] = {0, 0, 0};
  if (!mpuOk || !mpuRead(a, g)) {
    mpuOk = mpuInit();  // try to recover from a loose wire
    setLed(255, 0, 255);  // purple = sensor problem
  }

  bool loud;
  int mic = micTakeLevel(loud);
  char line[128];
  int len = snprintf(line, sizeof(line), "%lu,%lu,%.3f,%.3f,%.3f,%.1f,%.1f,%.1f,%d,%d,%d,%d\n",
                     (unsigned long)seq++, (unsigned long)millis(),
                     a[0], a[1], a[2], g[0], g[1], g[2], mic,
                     !digitalRead(BTN_A_PIN), !digitalRead(BTN_B_PIN), loud);
  memcpy(batchBuf + batchLen, line, len);
  batchLen += len;
  if (++batchCount >= BATCH) {
    udp.beginPacket(laptop, DATA_PORT);
    udp.write((const uint8_t*)batchBuf, batchLen);
    udp.endPacket();
    batchLen = batchCount = 0;
  }

  if (seq % 50 == 0) Serial.print(line);  // one line per second on USB serial for debugging
}
