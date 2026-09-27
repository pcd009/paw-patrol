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
 │  + mic (loud = it's us)  │                 │     walking/trotting/sniffing)   │
 │  + button (event marker) │                 │  + webcam  → where the dog is    │
 │  + RGB LED (status)      │ ◄────────────── │  + laptop mic → bark / whine     │
 │  + power bank / LiPo     │   LED commands  │  → rules (alerts) + Claude       │
 └──────────────────────────┘                 │     (explanation)                │
                                              └──────────────────────────────────┘
```

**Hardware's job:** a small unit on the dog's harness that streams motion + sound level to the laptop, and an LED that
shows the alert state. The laptop does all the "thinking".

### What we use, and what we skip

| Part | Use? | Why |
|---|---|---|
| ESP32-C6 Glyph | ✅ | The collar brain; sends data over Wi-Fi |
| MPU6050 | ✅ **most important** | Motion + posture: walking, trotting, lying, sitting |
| RGB LED | ✅ | Shows the rule state on the dog (green/amber/red). Looks good on video |
| Push button A | ✅ | "Event marker": the handler presses it when the dog changes activity (these presses become our labels) |
| Push button B | Spare | |
| Condenser mic + 2× 2.2k | ✅ simple use | Detects loud sounds *at the dog*: tells us whether a bark came from our dog or somewhere else. (Recognising bark vs whine is the laptop mic's job; without an amplifier this mic can't do that.) |
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

## 2b. Wiring / soldering diagram

Pin names are the labels **printed on the Glyph board** (GPIO number in brackets). Every "3.3V" and "GND" can go
to any 3.3V / GND pin on the board, or to the breadboard + / − rails fed from them.

```
  PART                   PART PIN                              GLYPH C6 PIN
 ┌───────────────┐
 │ MPU6050       │  VCC ────────────────────────────────────── 3.3V
 │ motion sensor │  GND ────────────────────────────────────── GND
 │               │  SDA ────────────────────────────────────── SDA  (IO4)
 │               │  SCL ────────────────────────────────────── SCL  (IO5)
 └───────────────┘  XDA, XCL, AD0, INT: not connected

 ┌───────────────┐
 │ RGB LED       │  long leg (common +) ────────────────────── 3.3V
 │ common anode  │  red   ───[resistor]─────────────────────── D18  (IO18)
 │               │  green ───[resistor]─────────────────────── D19  (IO19)
 │               │  blue  ───[resistor]─────────────────────── D20  (IO20)
 └───────────────┘  resistor = 220–330 Ω each (firmware dims the LED if you have none)

 ┌───────────────┐
 │ Mic           │  +  (pin NOT touching the case) ──┬──[2.2k]── 3.3V
 │ electret      │                                   └────────── A2   (IO2)
 │               │  −  (pin touching the metal case) ──────────── GND
 └───────────────┘  weak signal? put both 2.2k in series (4.4k)

 ┌───────────────┐
 │ Button A      │  one leg ────────────────────────────────── D6   (IO6)
 │ event marker  │  diagonally opposite leg ────────────────── GND
 └───────────────┘
 ┌───────────────┐
 │ Button B      │  one leg ────────────────────────────────── A3   (IO3)
 │ spare         │  diagonally opposite leg ────────────────── GND
 └───────────────┘

 Power: USB-C from the laptop or a power bank.
 Onboard LED (IO14): flashes when the mic hears a loud sound.
 Leave free: IO8, IO9 (boot mode), IO12, IO13 (USB).
```

**Same thing as a table, by board pin:**

| Glyph pin | Connected to |
|---|---|
| 3.3V | MPU6050 VCC · LED long leg · 2.2k resistor to mic + |
| GND | MPU6050 GND · mic − · Button A · Button B |
| SDA (IO4) | MPU6050 SDA |
| SCL (IO5) | MPU6050 SCL |
| A2 (IO2) | Mic + |
| A3 (IO3) | Button B |
| D6 (IO6) | Button A |
| D18 (IO18) | LED red (via resistor) |
| D19 (IO19) | LED green (via resistor) |
| D20 (IO20) | LED blue (via resistor) |

4-leg push buttons: the two legs on the *same side* (the ones facing each other across the gap) are always
connected. Use two **diagonally opposite** legs so the button actually switches.

**Check before soldering:** on the breadboard, type `calm` in the receiver. It must be **green**. If it's blue,
the green and blue legs are swapped: swap those two wires (a common mix-up, since the legs sit side by side).

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

### Step 3: Wire the LED and buttons (10 min)

| Part | Connect |
|---|---|
| RGB LED red leg | resistor → **IO18 (D18)** |
| RGB LED green leg | resistor → **IO19 (D19)** |
| RGB LED blue leg | resistor → **IO20 (D20)** |
| RGB LED long leg (common) | **GND** if it's common-cathode, **3.3V** if it's common-anode (see below) |
| Button A (event marker) | one side → **IO6 (D6)**, other side → **GND** |
| Button B (spare) | one side → **IO3 (A3)**, other side → **GND** |

- No pull-up resistors are needed for the buttons; the ESP32 has them built in.
- **Which kind of LED do we have?** Try the long leg on GND first. If the colours come out inverted
  (on when they should be off), move the long leg to 3.3V and set `LED_COMMON_ANODE = true` in the firmware.
- **Don't use IO8 or IO9**: they control boot mode and the board may not start.

### Step 4: The mic (10 min)
**Its one job: "was that sound from OUR dog?"** The laptop mic recognises bark vs whine. The mic on the harness sits
right next to the dog's throat, so when it reads *loud* at the same moment, the sound almost certainly came from our
dog and not from the TV or a dog outside. That's a nice bit of context for Claude, and a nice line for the pitch.

| Mic | Connect |
|---|---|
| Mic **+** (the pin *not* connected to the metal case) | 2.2k resistor to **3.3V**, **and** a wire to **IO2 (A2)** |
| Mic **−** (connected to the metal case) | **GND** |

How it works (no amplifier needed for this):
- The ESP32 reads the mic as fast as it can between motion samples and reports how much the signal swung
  in each 20 ms slot (`mic` column).
- It learns the background noise level by itself. When a sound is ~3× louder than the background, it sets
  `loud = 1` and **flashes the onboard LED** (so you can see it working on video).
- The receiver prints `🔊 LOUD at collar ...` for each loud event.

**Test:** run the receiver, stay quiet for 5 s (so it learns the background), then clap or bark next to it →
`🔊 LOUD` appears and the onboard LED flashes. Talking at normal volume from 1 m away should *not* trigger it.

**Signal too weak?** (the `mic` value barely changes when you clap): put **both 2.2k resistors in series**
(4.4k total) between 3.3V and mic +. That roughly doubles the signal. Still nothing after 10 min? Set
`USE_MIC = false` and move on; the laptop mic still covers barks.

**Too sensitive?** (LOUD fires all the time on the dog's own movement): raise `LOUD_FACTOR` (e.g. 3.0 → 5.0) at the
top of the firmware.

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
- Clap near the mic: `🔊 LOUD at collar` appears + the onboard LED flashes
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
python tools/collar_receiver.py --out sessions/dryrun
```
At the same time, the SW person records webcam + mic.
If anything breaks now, it would have broken with the dog too, so fix it now.

---

## 4. The dog session (we only get a few minutes, so make them count)

**Before the dog arrives:** hotspot on, receiver running with `--out sessions/dog1`, webcam recording,
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
| 8 | Vocalisation: a doorbell sound on a phone, or a treat held just out of reach. **Also play a dog-bark video on a phone ~2 m away while the dog is quiet:** the laptop hears a bark but the collar stays quiet, which is exactly the "not our dog" case for the demo | 30 s |
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
| `mic` always 0 | `USE_MIC` is false, or the mic wire isn't on IO2 |
| `mic` jumps around with no sound | Mic wire loose, or mic + and − swapped |
| LED colours inverted, or **bright white** when it should be green | Common-anode LED (long leg on 3.3V): `LED_COMMON_ANODE = true` (the default now). If your long leg is on GND, set it to `false` |
| Rate ~24 Hz, "no data for 1 s" every other line, `mic` stuck at 0 | The Wi-Fi is too busy for the collar to keep up. Make sure the latest firmware is uploaded (it sends 5 samples per packet). If it still happens, switch to the **laptop's Mobile hotspot** (set to 2.4 GHz) instead of the venue Wi-Fi |
| Collar stops when unplugged from the laptop | Power bank turned itself off (too little current). Use a different power bank, or one with a "low current / always on" mode |

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
- `tools/collar_receiver.py`: live readout + saves everything to files + LED control from the laptop
- `PLAN.md`: the overall 3-hour plan (hardware + software + deck)

## 8. For the software track: reading the collar data

> The complete, up-to-date interface spec is in **`docs/COLLAR_DATA.md`**, with helpers in `tools/collar_data.py`.
> This section is a short summary.

The receiver always saves to a session folder: `sessions/<date_time>/` by default, or the folder given with
`--out sessions/dog1`. **`sessions/current.txt`** holds the path of the folder being written right now.
All files are written live, so they're safe to read while the receiver runs.

| File | What's in it | Use it for |
|---|---|---|
| `imu.csv` | Every sample, 50 per second | Activity classification (sliding 2 s windows), replaying a session |
| `events.jsonl` | One JSON object per line: `marker` (button A), `loud`, `led` (every LED change, whoever caused it), `collar_connected`, `collar_lost` | Timeline events, ground-truth labels, rules ("collar data lost"), the rule-alert history for the deck |
| `live.json` | Latest sample + `mag`, `pitch`, `roll`, `rate_hz`, `led`, `collar_ip`, rewritten 10× per second | Dashboard gauges, a quick "is it alive" check |

**`imu.csv` columns:**
`t_laptop, seq, ms, ax, ay, az (g), gx, gy, gz (deg/s), mic, btnA, btnB, loud`
- `t_laptop` = Unix time on the laptop (seconds, ms precision). **This is the sync key:** record the webcam/mic with
  laptop timestamps too (`time.time()` in Python) and join on it.
- `ms` = the collar's own clock (ms since boot). Evenly spaced every 20 ms; use it for exact gaps between samples.
- `events.jsonl` and `live.json` also carry `time`, the same laptop time in readable form (`2026-09-27 13:48:13.133`),
  and every console line starts with `[HH:MM:SS.mmm]`.
- **Sync check at the start of every session:** clap once in front of the webcam while pressing button A.
  The `marker` event's `t` and the clap in the video/audio should line up; if not, shift by the difference.
- `mic` = sound level at the collar over the last 20 ms; `loud` = 1 when it's well above background.
- This sensor reads ~1.2 g at rest (instead of 1.0). Calibrate on the first ~10 s of standing still.

**`events.jsonl` example:**
```json
{"t": 1790496054.476, "type": "marker"}
{"t": 1790496054.882, "type": "loud", "mic": 300}
{"t": 1790496055.120, "type": "led", "state": "ALERT"}
```
`led` states: `CALM`, `ATTN`, `ALERT`, `OFF`, `RGB r g b`, `SENSOR_FAULT`. The collar itself reports each change,
so it's logged even when your code sends the command straight to the collar.

**Reading it from Python:**
```python
import json, pandas as pd
session = open("sessions/current.txt").read().strip()
imu = pd.read_csv(f"{session}/imu.csv")                                   # whole session so far
events = [json.loads(l) for l in open(f"{session}/events.jsonl")]
live = json.load(open(f"{session}/live.json"))                            # latest reading
```
To follow `imu.csv` live, remember how many lines you've read and read only the new ones on each pass
(or `tail -f` it).

**Setting the collar LED from your code** (e.g. when a rule fires): send a UDP packet to the collar.
```python
import json, socket
live = json.load(open(f"{session}/live.json"))
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"ALERT", (live["collar_ip"], live["cmd_port"]))
# b"CALM" = green, b"ATTN" = amber, b"ALERT" = red, b"OFF", or b"LED 255 0 128"
```
