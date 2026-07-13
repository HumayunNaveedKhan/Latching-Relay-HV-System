# Meter‑Triggered Contactor Latching Controller
### ESP32‑C3 · Opto‑Isolated Sensing · Fotek SSR · Firmware Latch

A field‑hardened controller that reads a utility energy meter's **remote connect / disconnect** signals and mirrors them onto an external **200 A contactor** — latching the contactor **ON** on a connect pulse and holding it until a disconnect pulse arrives. Built to survive a heavy industrial environment (ACs, motors, contactor switching) without hanging or false‑triggering.

> **Status:** Deployed and load‑tested (3 working circuits). Firmware, diagnostics, circuit diagram and this documentation are in the repo.
> **Author:** Humayun Naveed Khan — R&D Engineer · **Client:** Thal Engineering (Korangi, Karachi).

---

## Table of Contents
1. [What it does](#1-what-it-does)
2. [System architecture](#2-system-architecture)
3. [Signal nature — the key finding](#3-signal-nature--the-key-finding)
4. [Detection & control logic](#4-detection--control-logic)
5. [Bill of materials (links · datasheets · images)](#5-bill-of-materials)
6. [Wiring & connections (how to rebuild)](#6-wiring--connections)
7. [Firmware & repository layout](#7-firmware--repository-layout)
8. [Quick start](#8-quick-start)
9. [Latching / normal relay study (why they were not used)](#9-latching--normal-relay-study)
10. [Troubleshooting](#10-troubleshooting)
11. [Safety](#11-safety)
12. [Circuit diagram](#12-circuit-diagram)

---

## 1. What it does

The energy meter operates its own **motorized breaker** on a web (dashboard) command. It exposes two **volt‑free (dry) signalling contacts** that pulse when it actuates:

- **CONNECT (ON)** — pulses when the meter closes/energizes.
- **DISCONNECT (OFF)** — pulses when the meter opens/de‑energizes.

This controller senses those two pulses and drives a **Fotek solid‑state relay (SSR)**, which switches the coil of an existing **200 A contactor**. The contactor therefore follows the meter: **connect pulse → contactor ON (held), disconnect pulse → contactor OFF (held).**

Because the two signals are *momentary* (the meter only pulses on a state change), the controller provides the **memory / latch** in firmware.

---

## 2. System architecture

```
                     ISOLATION BARRIER (opto)                 ISOLATION (inside SSR)
                              │                                        │
  ┌───────────┐   ON/COM      │   ┌──────────────┐    GPIO1/3   ┌──────────────┐  GPIO10   ┌───────────┐   1–2   ┌──────────────┐
  │  ENERGY   │──────────────►│──►│  PC817  ×2   │─────────────►│  ESP32‑C3    │──────────►│ Fotek SSR │───────►│  200 A       │──► LOAD
  │  METER    │   OFF/COM      │   │ opto‑isolate │  (active‑low)│  SuperMini   │(active‑high)│  SSR‑40DA │  coil  │  CONTACTOR   │
  └───────────┘  (dry, pulse)  │   └──────────────┘              │  latch + WDT │           └───────────┘         └──────────────┘
                              │                                 └──────────────┘
     GND_OPTO  (field)        │        GND_ESP  (logic)                                         GND_LOAD  (mains/coil)
     ── 3 isolated supplies · 3 separate grounds · optos + SSR are the only crossings ──
```

**Signal path:** meter dry contact → optocoupler LED (isolated) → phototransistor pulls an ESP GPIO **LOW** → ESP detects a *sustained* LOW → SR latch → GPIO10 drives the SSR **HIGH** → SSR switches the 220 V contactor coil.

**Three isolated power domains** (never share a ground):
| Domain | Supply | Powers |
|---|---|---|
| `GND_ESP` | HLK‑5M05 (5 V) | ESP32‑C3 + opto phototransistor side |
| `GND_OPTO` | HLK‑5M05 (5 V), separate | Opto LEDs + meter contacts (field side) |
| `GND_LOAD` | HiLink (coil voltage) | Contactor coil switched by the SSR |

---

## 3. Signal nature — the key finding

This was the crux of the whole project, so it is documented in full. **The signal looks completely different before and after the optocoupler.**

### 3.1 Raw signal (bare wire, directly on the meter contact)
Each command is **not** a single clean closure. It is a **dense burst of extremely short pulses**:

| Parameter | CONNECT (ON) | DISCONNECT (OFF) |
|---|---|---|
| Individual pulse width | 5 – 43 µs | 5 – 113 µs |
| Closes per command | ~4,000 over ~6 s | ~3,000 over ~3 s |
| Rate | ~700 closes/s | ~700–1,000 closes/s |
| Idle | ~0 closes/s (silent) | ~0 closes/s (silent) |

So on bare wires the signal is a **firehose of microsecond pulses** — impossible to treat as a simple contact and very vulnerable to being confused with EMI.

### 3.2 Post‑optocoupler signal (what the ESP actually sees)
The PC817 optocoupler is deliberately "slow" (rise/fall ≈ 4–18 µs). It **integrates** the microsecond burst into **one clean, sustained closure**:

| Parameter | CONNECT (ON) | DISCONNECT (OFF) |
|---|---|---|
| Line held LOW for | **≈ 6 s** | **≈ 3 s** |
| Shape | one 6 s LOW, *or* 3 s + brief blip + 3 s | one clean 3 s LOW |
| Consistency | very consistent | very consistent (3000–3010 ms) |
| Mutually exclusive? | Yes — ON low ⇒ OFF open, always | Yes |

**Why CONNECT (~6 s) is longer than DISCONNECT (~3 s):** the motorized breaker's *close + spring‑charge* sequence takes longer than the *open*. The occasional "3 s + blip + 3 s" on CONNECT is a single 6 s event where the opto momentarily releases mid‑operation — **not** two separate commands.

**Why some disconnects were "missed":** in a few tests the meter/breaker did **not** actuate on the first web press (a second press was needed). Those were **meter‑side non‑events** — every closure that physically happened was captured. Nothing is dropped on the controller side; a 3‑second LOW is impossible to miss with any polling.

### 3.3 Consequence for the design
Because the post‑opto signal is a **sustained level of ≥ 3 s**, detection collapses to one bullet‑proof rule:

> **A sense line held LOW for longer than a threshold (800 ms) = that command.**

Real signals are ≥ 3000 ms; an *isolated* opto cannot be held LOW by EMI (its LED needs real, sustained current). The 800 ms threshold sits ~4× under the real signal and effectively infinitely above any noise glitch.

---

## 4. Detection & control logic

The **final** firmware (`hexing_controller_sustained_final.ino`) uses **sustained‑LOW detection** + an idempotent **SR latch**:

- **CONNECT:** GPIO1 LOW ≥ `THRESHOLD_MS` (800 ms) → latch **ON** → SSR ON (contactor energized).
- **DISCONNECT:** GPIO3 LOW ≥ `THRESHOLD_MS` → latch **OFF** → SSR OFF.
- **Idempotent:** re‑confirming the current state does nothing; fires once per command and holds until the opposite one.
- **SSR is active‑high:** GPIO10 HIGH = SSR on. Boots LOW (safe OFF).

**Reliability features (all in firmware):**
- **No interrupts** — polling only, so an EMI *interrupt storm* can never hang the CPU.
- **Non‑blocking loop** — no `delay()` in the hot path.
- **Hardware watchdog** — if anything ever locks the loop, the chip auto‑resets within `WDT_TIMEOUT` (8 s).
- **Flash state persistence** — the last ON/OFF state is saved and restored on boot, so a reset returns the contactor to where it was.
- **Reset‑reason reporting** — every boot prints why it last reset (`BROWNOUT` = supply, `PANIC`/`WDT` = EMI), which pinpoints any hardware issue.
- **Serial overrides** — `1` = ON, `0` = OFF, `t` = toggle (for testing without the meter).

> The repo also contains earlier detection strategies kept for reference: **burst‑density** detection (for the *bare‑wire* firehose signal) and an **integrator/debounce** approach. The **sustained‑LOW** version is the deployed one, matched to the post‑opto signal.

---

## 5. Bill of materials

> Product photos live in `docs/img/`. Drop the corresponding photo next to each row (filenames suggested below). Manufacturer/datasheet links are also good sources of reference images.

| Ref | Component | Qty | Key spec | Datasheet | Buy / info | Photo |
|---|---|---|---|---|---|---|
| U1 | **ESP32‑C3 SuperMini** | 1 | RISC‑V, Wi‑Fi/BLE, 3.3 V logic, USB‑C | [ESP32‑C3 datasheet (Espressif)](https://www.espressif.com/documentation/esp32-c3_datasheet_en.pdf) | search "ESP32‑C3 SuperMini" | `docs/img/esp32c3_supermini.jpg` |
| U2, U3 | **PC817 optocoupler** | 2 | 4‑pin DIP, 5 kV isolation, CTR 50–600%, tr/tf ≈ 4–18 µs | [PC817 datasheet (Sharp)](https://www.farnell.com/datasheets/73758.pdf) | any distributor | `docs/img/pc817.jpg` |
| K1 | **Fotek SSR‑40DA** | 1 | DC→AC SSR, in **4–32 VDC**, out 24–380 VAC, 40 A, zero‑cross, leak ≤5 mA | [SSR‑40DA datasheet (PDF)](https://handsontec.com/dataspecs/discrete/SSR-40DA.pdf) · [Fotek product page](https://www.fotek.com.tw/en-gb/product-category/143) | Fotek / distributors | `docs/img/fotek_ssr40da.jpg` |
| PS1, PS2 | **HLK‑5M05** AC‑DC module (5 V/5 W) | 2 | 85–264 VAC in, 5 V/1 A out, 3 kV isolation | [HLK‑5M05 datasheet](https://agelectronica.lat/pdfs/textos/H/HLK-5M05.PDF) · [Hi‑Link](https://www.hlktech.net/index.php?id=115) | [LCSC](https://www.lcsc.com/product-detail/AC-DC-Power-Modules_HI-LINK-HLK-5M05_C209907.html) | `docs/img/hlk5m05.jpg` |
| R1, R2 | Resistor **330 Ω** (opto LED) | 2 | ≥1/4 W | generic | — | — |
| R3, R4 | Resistor **10 kΩ** (pull‑up, optional) | 2 | ≥1/4 W (internal `INPUT_PULLUP` also works) | generic | — | — |
| — | **200 A contactor** (existing) | 1 | 220 VAC coil | your contactor's datasheet | existing | `docs/img/contactor.jpg` |
| — | **RC snubber** across coil | 1 | 0.1 µF X2 + ~100 Ω (or ready snubber) | — | — | — |
| — | Veroboard, 2.54 mm headers, screw terminals, hookup wire | — | — | — | — | — |

**R&D‑only components** (used during development, **not** in the deployed latching circuit): `ZMPT101B` voltage‑sensing modules (×2), 2‑channel SONGLE relay module, 1‑channel high/low‑trigger relay module. Kept for reference in `docs/`.

---

## 6. Wiring & connections

> **Golden rule:** `GND_ESP`, `GND_OPTO` and `GND_LOAD` are **three independent grounds**. The optocouplers and the SSR are the **only** electrical crossings. Commoning any two grounds re‑introduces the EMI/ground‑bounce that this design exists to eliminate.

### 6.1 Pin map (ESP32‑C3)
| Pin | Net | Function |
|---|---|---|
| `5V` | +5 V (PS1) | Board power |
| `GND` | `GND_ESP` | Logic ground |
| `GPIO1` | `ON_SENSE` | CONNECT input (active‑low, `INPUT_PULLUP`) |
| `GPIO3` | `OFF_SENSE` | DISCONNECT input (active‑low, `INPUT_PULLUP`) |
| `GPIO10` | `SSR_CTRL` | SSR drive (active‑high) |

> Avoid strapping pins **GPIO2, GPIO8, GPIO9**; USB pins are **GPIO18/GPIO19**.

### 6.2 Optocoupler sense front‑end (per channel — repeat for OFF)
PC817 pins: **1 = LED anode, 2 = LED cathode, 3 = emitter, 4 = collector.**

```
FIELD SIDE (GND_OPTO domain)                 LOGIC SIDE (GND_ESP domain)
  +5V_OPTO ── 330Ω ── PC817·1 (anode)        PC817·4 (collector) ── GPIO1  (+10k to 3V3 or use INPUT_PULLUP)
  PC817·2 (cathode) ── meter COM             PC817·3 (emitter)  ── GND_ESP
  meter ON terminal ── GND_OPTO
```
- **Behaviour:** contact closes → LED lights → phototransistor conducts → GPIO pulled **LOW** = "closed". Idle = HIGH. (Active‑low — matches the firmware.)
- **Polarity note:** as wired here the meter **COM** goes to the cathode and the **ON/OFF terminal** goes to field ground. Either orientation of "COM vs terminal" works **as long as the LED loop completes** — but the LED itself is polarity‑sensitive: **anode (pin 1) must be on the resistor/+5 V side.**
- Repeat identically for the **OFF** channel into **GPIO3** with a second PC817.

### 6.3 SSR & contactor
Fotek SSR terminals: **3 = DC+, 4 = DC−, 1 & 2 = AC load.**
```
GPIO10 ─────────► SSR‑3 (DC+)          [ACTIVE‑HIGH: GPIO HIGH = SSR ON]
GND_ESP ────────► SSR‑4 (DC−)
Live (coil sup) ► SSR‑1
SSR‑2 ──────────► contactor coil A1
contactor A2 ───► Neutral (coil sup)
RC snubber ─────► across coil A1–A2
```

> ⚠️ **SSR‑40DA control‑voltage caveat.** The genuine SSR‑40DA spec is **4–32 VDC** input with **turn‑off < 3.5 V**. The ESP GPIO high is only **3.3 V**, which is *below* the 4 V minimum. Many clone units still trigger at ~3 V (the deployed unit did), but if your SSR does **not** switch reliably from 3.3 V, drive it from **5 V (or 12 V) through a small NPN/MOSFET** gated by GPIO10 instead. A floating SSR input = OFF, so boot is safe either way.

### 6.4 Power
- **PS1 (HLK‑5M05)** → ESP `5V`/`GND` (`GND_ESP`). Add a **470–1000 µF** bulk cap + 0.1 µF at the board for supply ride‑through.
- **PS2 (HLK‑5M05)** → opto LED side (+5 V_OPTO / `GND_OPTO`). Its ground ties to the meter COMs **only**.
- **Coil supply (HiLink)** → the contactor‑coil circuit switched by the SSR (`GND_LOAD`).

---

## 7. Firmware & repository layout

```
/
├── firmware/
│   ├── hexing_controller_sustained_final.ino   ← DEPLOY THIS (opto + SSR, sustained‑LOW latch)
│   ├── hexing_controller_ssr.ino               ← density‑detect + SSR (pre‑opto strategy)
│   ├── hexing_controller_burst_final.ino       ← burst‑density detection (bare‑wire signal)
│   ├── hexing_controller_emi_robust.ino        ← integrator/debounce EMI‑rejection variant
│   ├── relay_robust_watchdog.ino               ← watchdog + state‑persistence bring‑up
│   └── relay_onoff_test_hightrigger.ino        ← simple relay ON/OFF bench test
├── diagnostics/
│   ├── meter_sniffer_bare.ino        ← print on any edge (fastest, no filter)
│   ├── meter_sniffer_min.ino         ← quiet counter, 1 line/sec
│   ├── meter_sniffer_sensitive.ino   ← no‑delay poll + pulse‑width timing
│   ├── meter_sniffer_lowtime.ino     ← longest‑LOW‑per‑second (post‑opto)
│   └── meter_sniffer_ground_proof.ino← self ground‑path test + sniff
├── hardware/
│   ├── board.py                      ← SKiDL netlist for the sense/control PCB
│   └── circuit_diagram.(png|pdf)     ← full schematic (see §12)
├── docs/
│   └── img/                          ← component & build photos
└── README.md
```

**Key config (top of the final sketch):**
| Constant | Default | Meaning |
|---|---|---|
| `THRESHOLD_MS` | 800 | Sustained‑LOW time to accept a command (real ≥ 3000 ms) |
| `WDT_TIMEOUT` | 8 | Seconds before a hung loop auto‑resets |
| `RESTORE_STATE` | true | Restore last ON/OFF on boot (set false if it ever boot‑loops) |

---

## 8. Quick start

1. **Board support:** Arduino IDE → install *esp32* core → select **ESP32C3 Dev Module** (USB‑CDC on Boot: Enabled).
2. **Flash** `firmware/hexing_controller_sustained_final.ino`.
3. **Wire** per §6 (opto front‑end, SSR, three separate grounds).
4. **Serial monitor @ 115200.** On boot it prints the reset reason and the restored state.
5. **Bench test without the meter:** send `1` (ON), `0` (OFF), `t` (toggle) — the SSR/contactor should follow.
6. **Live test:** command a real **connect** then **disconnect** from the meter dashboard (change the state — the meter only pulses on a change). The serial log prints `CONNECT (SSR ON)` / `DISCONNECT (SSR OFF)` and the contactor latches accordingly.

---

## 9. Latching / normal relay study

As an add‑on, standalone **latching / impulse relays** and a **normal‑relay DOL hold** were evaluated as a no‑microcontroller alternative. **Conclusion: not feasible as a drop‑in** for this meter without added buffering electronics. Findings:

| Device tested | Why it does **not** fit |
|---|---|
| Schneider/Merlin‑Gerin **TL / iTL** (e.g. A9C30811) | Single‑input **toggle** — flips on every pulse; cannot tell CONNECT from DISCONNECT. Also 230 VAC coil (not the available 12 V). |
| Hager **EPN 518** | Single‑input toggle, 24 VAC coil. Two contacts, but no separate ON/OFF **control** inputs. |
| ABB **E290‑16‑10/230** | Single‑input latch + manual I/O handle; **230 V** coil. |
| AEG **Elfa VFSS 16** | It is a **staircase timer**, not a latch — it drops itself after a time; 230 V coil. |

**Root reasons:**
1. **Wrong logic.** The job needs **two separate electrical inputs** (a connect pulse that can *only* set ON, a disconnect pulse that can *only* set OFF). Only a relay with dedicated **central ON/OFF inputs** — the Schneider **iTLc** — has this. Common stock impulse relays are single‑input toggles.
2. **Coil voltage.** Most stock units are 230 VAC coils; only 12 V / 5 V were available on site.
3. **Meter contact rating.** The meter's ON/OFF outputs are **low‑current, opto‑isolated electronic outputs**. A relay coil pulls **100–300 mA** — driving it directly from the meter contact would exceed its rating and risk the meter. Any relay approach therefore **still needs an opto + transistor buffer** — i.e. the same front‑end as the deployed design.

**What would work electromechanically:** a Schneider **Acti9 iTLc, 12 V DC, 1P 1NO** (central ON/OFF inputs) buffered through opto + transistor; *or* a **two‑relay DOL seal‑in** (RL memory relay + RS stop relay) with the same opto/transistor front‑end. Both are documented in the repo, but neither is simpler or more reliable than the deployed **opto + ESP32‑C3 + SSR** — which additionally gives logging, state memory, and a serial override. **The firmware latch is the chosen, deployed solution.**

---

## 10. Troubleshooting

Ordered roughly by how the project actually failed and was fixed.

| Symptom | Likely cause | Fix |
|---|---|---|
| **ESP hangs; serial dead until unplugged** | EMI latch‑up / interrupt storm / brownout from contactor & motor switching | Use the **SSR** (no arc), **opto‑isolate** sense lines with a **separate supply**, star‑ground + **bulk cap** + ferrites; keep the **watchdog** on. Read the boot **reset reason**. |
| **Relay self‑toggles every few seconds** | EMI on bare high‑impedance pull‑up sense lines being read as signals | Opto‑isolation + **sustained‑LOW / density** detection (not single‑edge). |
| **`Guru Meditation … Interrupt wdt timeout`** | Edge‑interrupt **storm** from EMI | Switch from interrupts to **polling** (the deployed firmware is poll‑based). |
| **Contactor won't drop out; connect works** | Hobby relay contacts **welded** by coil inrush arcing | **RC snubber** across the coil **and** replace the mechanical relay with the **SSR** (no contacts to weld). |
| **No signal, but a jumper works** | Meter **COM on wrong ground**, opto **LED reversed**, opto **field supply off**, or **meter not firing** | Verify COM → correct ground; anode (pin 1) on +5 V side; jumper **at the meter terminals**; command the **opposite** state to force a pulse. |
| **"Worked yesterday, nothing today"** | A **COM→GND wire popped**, or meter commanded to a state it's already in | Check COM‑to‑ground continuity; force a real ON→OFF (meter only pulses on a **change**). |
| **Boot reason = `BROWNOUT`** | Supply dips when the coil/relay switches | Separate relay/SSR supply, **1000 µF** bulk cap on the ESP rail; consider a small UPS/battery on the ESP. |
| **Boot reason = `PANIC` / `WDT`** | EMI hang | Opto isolation, coil snubber, series‑R + small cap on sense lines, ferrites. |
| **SSR won't switch from 3.3 V** | SSR‑40DA input min is **4 V** | Drive the SSR from **5 V/12 V via a transistor**, or confirm your unit triggers at 3.3 V. |
| **Relay/SSR glitches ON at boot** | GPIO floats for an instant at reset | Add a pull‑up/down on the control input to hold the safe state until firmware drives it. |
| **False triggers still slip through** | Threshold too low / residual noise | Raise `THRESHOLD_MS` (up to ~2000 ms; real signal is ≥3000 ms). |

**Diagnostic workflow:** flash a sniffer from `diagnostics/`, watch the serial output.
- `meter_sniffer_ground_proof.ino` → confirms the pin can be pulled LOW (proves pin/pull‑up/ground path).
- `meter_sniffer_bare.ino` / `_min.ino` → shows raw closures the instant they happen.
- `meter_sniffer_lowtime.ino` → shows the **longest LOW per second** — the number the final threshold is built on.

---

## 11. Safety

- **220 V mains** is present on the SSR load side and the contactor coil. Wiring must be done by a qualified person, with proper **insulation, clearance/creepage**, and enclosure.
- **Isolation is the design.** Keep the three grounds separate; keep the coil **RC snubber** fitted; do not bypass the optocouplers.
- **Utility meters are sealed.** Opening a utility‑owned meter may break a seal and constitute tampering (a legal offence in many jurisdictions), and exposes mains and a lithium cell. **Work only from the meter's designated external ON/OFF/COM terminals** — do not open or modify the meter.

---

## 12. Circuit diagram

Full schematic (meter → optocouplers → ESP32‑C3 → SSR → contactor, with the three isolated supplies and grounds):

- 📐 `hardware/circuit_diagram.png` · `hardware/circuit_diagram.pdf`
- Editable netlist: `hardware/board.py` (SKiDL → KiCad → Gerber).

```
![Circuit diagram](hardware/circuit_diagram.png)
```
*(Add the exported diagram image to `hardware/` and the line above will render it inline.)*

---

### Credits
Design, development, firmware and documentation by **Humayun Naveed Khan (R&D Engineer)** for **Thal Engineering**. Diagnostic firmware, feasibility study, and this README are provided so the system can be understood, maintained, and rebuilt from scratch.
