# PawPatrol: Hardware Getting-Started Guide

A step-by-step guide for building the collar and getting its data onto the laptop.
Follow it top to bottom. Each step ends with a **✅ Check**. Don't move on until the check passes.

---

## 1. What we're building (in one picture)

```
   ON THE DOG (harness)                         ON THE LAPTOP
 ┌──────────────────────────┐   Wi-Fi (UDP)   ┌──────────────────────────────────┐
 │ ESP32-C6 (Glyph)         │ ──────────────► │ collar_receiver.py               │
 │  + MPU6050 motion sensor │   50 samples/s  │  → activity (lying/sitting/      │
 │  + button (event marker) │                 │     walking/trotting/sniffing)   │
 │  + RGB LED (status)      │ ◄────────────── │  + webcam  → where the dog is    │
 │  + power bank / LiPo     │   LED commands  │  + laptop mic → bark / whine     │
 └──────────────────────────┘                 │  → rules (alerts) + Claude       │
                                              │     (explanation)                │
                                              └──────────────────────────────────┘
```

**Hardware's job:** a small unit on the dog's harness that streams motion data to the laptop, and an LED that
shows the alert state. The laptop does all the "thinking".

### What we use, and what we skip

| Part | Use? | Why |
|---|---|---|
| ESP32-C6 Glyph | ✅ | The collar brain; sends data over Wi-Fi |
| MPU6050 | ✅ **most important** | Motion + posture: walking, trotting, lying, sitting |
| RGB LED | ✅ | Shows the rule state on the dog (green/amber/red). Looks good on video |
| Push button A | ✅ | "Event marker": the handler presses it when the dog changes activity (these presses become our labels) |
| Toggle switch | ✅ | Recording on/off |
| Push button B | Spare | |
| Condenser mic + 2× 2.2k | ⚠️ optional | Without an amplifier the signal is too weak to recognise barks. At best it tells us "something loud happened". **Real bark detection uses the laptop mic.** |
| 16×2 LCD | ❌ skip | It can't go on a dog and we only have one ESP32 |
| Breadboard | Bench testing only | Too big and too loose for a dog. On the harness we wire directly with F-F jumpers |
| Webcam + RTX 3050 | ✅ (SW track) | Where the dog is (door, bed, bowl) + video for the demo |

### ⚠️ Missing part: LED resistors
The RGB LED needs a **220–330 Ω resistor on each colour leg** (3 in total). The firmware caps brightness to be safe,
but please **borrow 3 resistors** from another team or the organisers. (If your LED is on a small PCB module with
4 pins marked R/G/B/−, the resistors are already built in and you're fine.)

### Buy or borrow if you can
- A small **USB power bank** + a USB-C cable (easiest way to power the collar on the dog)
- A **dog harness**, electrical tape, zip ties, a small pouch or an old sock

---

## 2. Two very important setup notes (read first!)

1. **Use a phone hotspot, not the venue Wi-Fi.** Venue Wi-Fi usually blocks devices from talking to each other.
   Connect **both** the laptop and the ESP32 to the same phone hotspot, set to **2.4 GHz** (the ESP32-C6 can't use 5 GHz).
   On iPhone, turn on "Maximise Compatibility".
2. **Run everything on Windows, not WSL.** WSL2 can't easily receive Wi-Fi packets from the ESP32 or open the webcam.
   Install Arduino IDE and Python on Windows itself.

---

## 3. Step-by-step

### Step 0: Laptop setup (10 min)
1. Install **Arduino IDE 2.3+** (Windows) from arduino.cc.
2. `File → Preferences → Additional Boards Manager URLs`, paste:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. Boards Manager (left sidebar) → search **esp32** → install **esp32 by Espressif** (version 3.1.0 or newer).
4. `Tools → Board → ESP32 Arduino → GLYPHC6` (or "ESP32C6 Dev Module").
5. `Tools → USB CDC On Boot → Enabled` (so Serial Monitor works over USB-C).
6. Install Python 3 on Windows (python.org). Nothing else is needed for the receiver script.
7. Turn on the phone hotspot and connect the laptop to it. Find the laptop's IP address:
   open `cmd` → `ipconfig` → "Wireless LAN adapter Wi-Fi" → **IPv4 Address** (e.g. `172.20.10.2`). Write it down.
8. Allow Python through the Windows firewall when it asks (tick **Private** networks).

### Step 1: Blink test (5 min)
1. Plug the Glyph into the laptop with USB-C.
2. `File → Examples → 01.Basics → Blink`, change `LED_BUILTIN` to `14`, click Upload.
3. If upload fails: hold **BOOT**, tap **RESET**, release BOOT, then upload again.

✅ **Check:** the onboard LED blinks.

### Step 2: Wire the MPU6050 (10 min, on the breadboard first)

| MPU6050 pin | Glyph C6 pin |
|---|---|
| VCC | **3.3V** (not USB/5V) |
| GND | **GND** |
| SDA | **SDA / IO4** |
| SCL | **SCL / IO5** |
| (XDA, XCL, AD0, INT) | leave unconnected |

### Step 3: Wire the LED, buttons and switch (10 min)

| Part | Connect |
|---|---|
| RGB LED red leg | resistor → **IO18 (D18)** |
| RGB LED green leg | resistor → **IO19 (D19)** |
| RGB LED blue leg | resistor → **IO20 (D20)** |
| RGB LED long leg (common) | **GND** if it's common-cathode, **3.3V** if it's common-anode (see below) |
| Button A (event marker) | one side → **IO6 (D6)**, other side → **GND** |
| Button B (spare) | one side → **IO3 (A3)**, other side → **GND** |
| Toggle switch | middle pin → **IO7 (D7)**, one outer pin → **GND** |

- No pull-up resistors are needed for the buttons; the ESP32 has them built in.
- **Which kind of LED do we have?** Try the long leg on GND first. If the colours come out inverted
  (on when they should be off), move the long leg to 3.3V and set `LED_COMMON_ANODE = true` in the firmware.
- **Don't use IO8 or IO9**: they control boot mode and the board may not start.

### Step 4 (optional, max 15 min): the mic
Only do this if everything else already works.

| Mic | Connect |
|---|---|
| Mic **+** (the pin *not* connected to the metal case) | 2.2k resistor to **3.3V**, **and** a wire to **IO2 (A2)** |
| Mic **−** (connected to the metal case) | **GND** |

Set `USE_MIC = true` in the firmware. The `mic` value should jump when you clap next to it.
If it barely moves, give up on it: the laptop mic handles barks.

### Step 5: Flash the collar firmware (10 min)
1. Open `firmware/collar/collar.ino` in Arduino IDE.
2. Edit the top section:
   ```cpp
   const char* WIFI_SSID = "your hotspot name";
   const char* WIFI_PASS = "your hotspot password";
   const char* LAPTOP_IP = "172.20.10.2";   // from Step 0
   ```
3. Upload. Open the Serial Monitor at **115200** baud. You should see:
   `MPU6050 found` → `Connected. ESP32 IP: ...` → one data line per second.
4. On the laptop (Windows terminal, in this folder):
   ```
   python tools/collar_receiver.py
   ```

✅ **Check (all of these):**
- The receiver shows about **50 Hz** and `dropped` stays low
- Board lying flat: `|a|` ≈ **1.00 g**
- Tilt the board: pitch/roll change
- Shake it: `|a|` jumps above 2 g
- Press button A: `*** EVENT MARKER ***` appears
- Type `alert` + Enter in the receiver: the LED turns red. `calm` turns it green

LED colours set by the firmware itself: **blue** = connecting to Wi-Fi, **purple** = motion sensor problem.

### Step 6: Move it off the breadboard and onto the harness (20 min)
1. Replace the breadboard with **F-F jumpers** straight from the MPU6050 to the Glyph. Wrap each joint in tape.
   Put the button and the LED on short jumpers so the handler can reach the button.
2. Mount the unit on the **top of the harness, between the shoulder blades**. It's more stable than a collar,
   and a dangling collar tag would add noise.
3. **The MPU6050 must not wobble.** Tape it flat and tight. Draw an arrow on it pointing toward the dog's head,
   and **write down which way X points** (it's printed on the MPU6050 board). The SW track needs this.
4. Power: a power bank + USB-C cable in a pouch/sock, zip-tied to the harness. (Or a 3.7 V LiPo on the JST jack.
   **Check polarity**, because reversed polarity destroys the board.)
5. Nothing sharp, nothing loose, no bare metal touching the dog. Total weight should feel trivial to the dog.

✅ **Check:** put the harness on a teammate's back or a backpack, walk, jog, sit and lie down. Clear differences
show up in the receiver.

### Step 7: Full dry run WITHOUT the dog (10 min)
Do the entire dog script (below) with a human wearing or carrying the harness, while recording:
```
python tools/collar_receiver.py --record sessions/dryrun_imu.csv
```
(create the `sessions` folder first). At the same time, the SW person records webcam + mic.
If anything breaks now, it would have broken with the dog too, so fix it now.

---

## 4. The dog session (we only get a few minutes, so make them count)

**Before the dog arrives:** hotspot on, receiver running with `--record sessions/dog1_imu.csv`, webcam recording,
phone filming from a second angle, LED green, **the power bank is charged**.

**First:** clap once in front of the webcam while pressing button A. That syncs the video with the sensor data.

**Script** (press **button A at every change**; one person handles the dog, the other runs the laptop and says the labels
out loud so the video captures them):

| # | What the dog does | ~Time |
|---|---|---|
| 1 | Stand still (calibration) | 10 s |
| 2 | Lie down | 20 s |
| 3 | Sit (on command or with a treat) | 15 s |
| 4 | Walk on the leash | 30 s |
| 5 | Trot / jog next to the handler | 20 s |
| 6 | Sniff: scatter a few treats on the floor | 20 s |
| 7 | Walk to the "door" spot and wait there | 20 s |
| 8 | Vocalisation: a doorbell sound on a phone, or a treat held just out of reach | 20 s |
| 9 | Settle / lie down again | 20 s |

Galloping probably won't happen on command, and that's fine: we say "supported, not demoed".

**Dog comfort comes first.** If the dog is stressed by the harness, stop. A calm 90-second recording is better than none.

**Afterwards:** check the CSV file has data (open it). Copy it + the video to at least two laptops.

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| Upload fails / port not found | Different USB cable (some are charge-only). Hold BOOT, tap RESET, release BOOT, upload |
| Serial Monitor blank | `Tools → USB CDC On Boot → Enabled`, re-upload |
| `MPU6050 NOT found` | VCC on 3.3V? SDA↔IO4, SCL↔IO5 not swapped? Loose jumper? Some boards use address 0x69: set `MPU_ADDR` |
| Stuck on blue LED (connecting) | Wrong SSID/password, or the hotspot is 5 GHz. Turn on 2.4 GHz / "Maximise Compatibility" |
| Serial shows data but the receiver shows nothing | Wrong `LAPTOP_IP` (re-check `ipconfig`), Windows firewall blocking Python, or you're running in WSL |
| Rate well below 50 Hz / many dropped | Move closer to the phone; the power bank may be going to sleep (some turn off at low current, so use a different one) |
| Purple LED | Motion sensor wire came loose. Re-tape |
| LED colours inverted | Set `LED_COMMON_ANODE = true` |

---

## 6. Who does what (team of 2)

| Time | Hardware person | Software person |
|---|---|---|
| 0:00–0:30 | Steps 0–5 (flash, wire, see data) | Webcam + laptop-mic recording, receiver → CSV |
| 0:30–0:50 | Step 6–7 (harness, dry run) | Dog detection with webcam + bark detection |
| ~0:50–1:00 | **Dog session** (handler) | **Dog session** (laptop + labels) |
| 1:00–2:15 | Help the SW track: tune the activity thresholds on the recorded CSV, LED feedback from the rules | Events, rules, Claude, dashboard |
| 2:15–3:00 | Record the demo, cut the video | Deck |

## 7. Files in this repo

- `firmware/collar/collar.ino`: the ESP32 code (edit the Wi-Fi + laptop IP at the top)
- `tools/collar_receiver.py`: live readout + CSV recording + LED control from the laptop
- `PLAN.md`: the overall 3-hour plan (hardware + software + deck)

**Data format** (one line per sample, 50 per second):
`seq, ms, ax, ay, az (g), gx, gy, gz (deg/s), mic, btnA, btnB, rec`
The recorded CSV also adds `t_laptop` (Unix time) as the first column, used to sync with the video.
