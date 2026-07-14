# Meter-Triggered Contactor Latching Controller
### ESP32-C3 · Opto-Isolated Sensing · Fotek SSR · Firmware Latch — 500 A Contactor

A field-hardened controller that reads a utility energy meter's **remote connect / disconnect** signals and mirrors them onto a **500 A contactor** — latching the contactor **ON** on a connect pulse and holding it until a disconnect pulse arrives. Built to survive a heavy industrial environment (ACs, motors, contactor switching) without hanging or false-triggering.

> **Status:** Deployed and load-tested (3 working circuits) on a 500 A contactor.
> **Author:** Humayun Naveed Khan — R&D Engineer · **Client:** Thal Engineering (Korangi, Karachi).
> **Repo:** [Latching-Relay-HV-System](https://github.com/HumayunNaveedKhan/Latching-Relay-HV-System)

---

## Table of Contents
1. [What it does](#1-what-it-does)
2. [System architecture](#2-system-architecture)
3. [Signal nature — the key finding](#3-signal-nature--the-key-finding)
4. [Detection & control logic](#4-detection--control-logic)
5. [Bill of materials](#5-bill-of-materials)
6. [Wiring & connections](#6-wiring--connections)
7. [Repository structure](#7-repository-structure)
8. [Quick start](#8-quick-start)
9. [Latching / normal relay feasibility study](#9-latching--normal-relay-feasibility-study)
10. [Troubleshooting](#10-troubleshooting)
11. [Safety](#11-safety)
12. [System range, capabilities & limitations](#12-system-range-capabilities--limitations)
13. [Other projects by the author](#13-other-projects-by-the-author)

---

## 1. What it does

The energy meter operates its own **motorized breaker** on a web (dashboard) command and exposes two **volt-free (dry) signalling contacts** that pulse when it actuates:

- **CONNECT (ON)** — pulses when the meter closes/energizes.
- **DISCONNECT (OFF)** — pulses when the meter opens/de-energizes.

This controller senses those two pulses and drives a **Fotek SSR-40DA** solid-state relay, which switches the coil of the site's **500 A contactor**. The contactor follows the meter: **connect pulse → contactor ON (held), disconnect pulse → contactor OFF (held).** The SSR only ever switches the contactor's low-current coil circuit — never the 500 A load path — so the same architecture scales to any contactor frame size without changing the control electronics.

Because the meter's two signals are *momentary*, the controller provides the **memory / latch** in firmware.

---

## 2. System architecture

```
                     ISOLATION BARRIER (opto)                 ISOLATION (inside SSR)
                              │                                        │
  ┌───────────┐   ON/COM      │   ┌──────────────┐    GPIO1/3   ┌──────────────┐  GPIO10   ┌───────────┐   1–2   ┌──────────────┐
  │  ENERGY   │──────────────►│──►│  PC817  ×2   │─────────────►│  ESP32-C3    │──────────►│ Fotek SSR │───────►│  500 A       │──► LOAD
  │  METER    │   OFF/COM      │   │ opto-isolate │  (active-low)│  SuperMini   │(active-high)│  SSR-40DA │  coil  │  CONTACTOR   │
  └───────────┘  (dry, pulse)  │   └──────────────┘              │  latch + WDT │           └───────────┘         └──────────────┘
                              │                                 └──────────────┘
     GND_OPTO  (field)        │        GND_ESP  (logic)                                         GND_LOAD  (mains/coil)
     ── 3 isolated supplies · 3 separate grounds · optos + SSR are the only crossings ──
```

**Three isolated power domains** (never share a ground):
| Domain | Supply | Powers |
|---|---|---|
| `GND_ESP` | HLK-5M05 (5 V) | ESP32-C3 + opto phototransistor side |
| `GND_OPTO` | HLK-5M05 (5 V), separate | Opto LEDs + meter contacts (field side) |
| `GND_LOAD` | Existing coil supply | 500 A contactor coil, switched by the SSR |

---

## 3. Signal nature — the key finding

This was the crux of the whole project. **The signal looks completely different before and after the optocoupler.**

### 3.1 Raw signal (bare wire, directly on the meter contact)
Each command is **not** a single clean closure — it's a **dense burst of extremely short pulses**:

| Parameter | CONNECT (ON) | DISCONNECT (OFF) |
|---|---|---|
| Individual pulse width | 5 – 43 µs | 5 – 113 µs |
| Closes per command | ~4,000 over ~6 s | ~3,000 over ~3 s |
| Rate | ~700 closes/s | ~700–1,000 closes/s |
| Idle | ~0 closes/s (silent) | ~0 closes/s (silent) |

![Raw signal — burst of short pulses](img/fig1_raw_signal_pulses.png)
*Illustrative rendering of the measured burst density and zoomed pulse widths (not a literal reproduction of every pulse).*

### 3.2 Post-optocoupler signal (what the ESP actually sees)
The PC817's finite response time **integrates** the burst into **one clean, sustained closure**:

| Parameter | CONNECT (ON) | DISCONNECT (OFF) |
|---|---|---|
| Line held LOW for | **≈ 6 s** | **≈ 3 s** |
| Shape | one 6 s LOW, *or* 3 s + brief blip + 3 s | one clean 3 s LOW |
| Consistency | very consistent | very consistent (3000–3010 ms) |
| Mutually exclusive? | Yes — ON low ⇒ OFF open, always | Yes |

![Sustained signal after opto-isolation](img/fig2_opto_sustained_signal.png)

**Why CONNECT (~6 s) is longer than DISCONNECT (~3 s):** the motorized breaker's close + spring-charge sequence takes longer than the open. The occasional "3 s + blip + 3 s" on CONNECT is a single 6 s event where the opto momentarily releases mid-operation — not two separate commands. A handful of "missed" disconnects during testing were meter-side non-events (the breaker didn't actuate on the first web press) — every closure that physically happened was captured.

![Signal metrics comparison](img/fig3_signal_metrics_bars.png)

### 3.3 Consequence for the design
Because the post-opto signal is a **sustained level of ≥ 3 s**, detection collapses to one rule:

> **A sense line held LOW for longer than a threshold (800 ms) = that command.**

Real signals are ≥ 3000 ms; an isolated opto's LED cannot be held conducting by EMI. The 800 ms threshold sits ~4× under the real signal and effectively infinitely above any noise glitch.

---

## 4. Detection & control logic

The **final** firmware (`main-v6-04-07-2026`) uses **sustained-LOW detection** + an idempotent **SR latch**:

- **CONNECT:** GPIO1 LOW ≥ `THRESHOLD_MS` (800 ms) → latch **ON** → SSR ON (contactor energized).
- **DISCONNECT:** GPIO3 LOW ≥ `THRESHOLD_MS` → latch **OFF** → SSR OFF.
- **Idempotent:** re-confirming the current state does nothing; fires once per command and holds until the opposite one.
- **SSR is active-high:** GPIO10 HIGH = SSR on. Boots LOW (safe OFF).

**Reliability features (all in firmware):**
- **No interrupts** — polling only, so an EMI *interrupt storm* can never hang the CPU.
- **Non-blocking loop** — no `delay()` in the hot path.
- **Hardware watchdog** — auto-resets the chip within 8 s if the loop ever stalls.
- **Flash state persistence** — the last ON/OFF state is saved and restored on boot.
- **Reset-reason reporting** — every boot prints why it last reset (`BROWNOUT` = supply, `PANIC`/`WDT` = EMI).
- **Serial overrides** — `1` = ON, `0` = OFF, `t` = toggle (for testing without the meter).

---

## 5. Bill of materials

| Ref | Component | Qty | Key spec | Datasheet | Buy / info |
|---|---|---|---|---|---|
| U1 | **ESP32-C3 SuperMini** | 1 | RISC-V, Wi-Fi/BLE, 3.3 V logic, USB-C | [ESP32-C3 datasheet (Espressif)](https://www.espressif.com/documentation/esp32-c3_datasheet_en.pdf) | search "ESP32-C3 SuperMini" |
| U2, U3 | **PC817 optocoupler** | 2 | 4-pin DIP, 5 kV isolation, CTR 50–600% | [PC817 datasheet (Sharp)](https://www.farnell.com/datasheets/73758.pdf) | any distributor |
| K1 | **Fotek SSR-40DA** | 1 | In 4–32 VDC, out 24–380 VAC, 40 A, zero-cross, leak ≤5 mA | [SSR-40DA datasheet (PDF)](https://handsontec.com/dataspecs/discrete/SSR-40DA.pdf) · [Fotek](https://www.fotek.com.tw/en-gb/product-category/143) | Fotek / distributors |
| PS1, PS2 | **HLK-5M05** AC-DC module (5 V/5 W) | 2 | 85–264 VAC in, 5 V/1 A out, 3 kV isolation | [HLK-5M05 datasheet](https://agelectronica.lat/pdfs/textos/H/HLK-5M05.PDF) | [LCSC](https://www.lcsc.com/product-detail/AC-DC-Power-Modules_HI-LINK-HLK-5M05_C209907.html) |
| R1, R2 | Resistor **330 Ω** (opto LED) | 2 | ≥1/4 W | generic | — |
| R3, R4 | Resistor **10 kΩ** (pull-up, optional) | 2 | ≥1/4 W (internal `INPUT_PULLUP` also works) | generic | — |
| — | **500 A contactor** (existing, site-deployed) | 1 | AC coil, existing installation | manufacturer datasheet | existing |
| — | **RC snubber** across coil | 1 | 0.1 µF X2 + ~100 Ω (or ready snubber) | — | — |

---

## 6. Wiring & connections

> **Golden rule:** `GND_ESP`, `GND_OPTO` and `GND_LOAD` are **three independent grounds**. The optocouplers and the SSR are the **only** electrical crossings.

### 6.1 Pin map (ESP32-C3)
| Pin | Net | Function |
|---|---|---|
| `5V` / `GND` | +5 V (PS1) / `GND_ESP` | Board power |
| `GPIO1` | `ON_SENSE` | CONNECT input (active-low, `INPUT_PULLUP`) |
| `GPIO3` | `OFF_SENSE` | DISCONNECT input (active-low, `INPUT_PULLUP`) |
| `GPIO10` | `SSR_CTRL` | SSR drive (active-high) |

> Avoid strapping pins **GPIO2, GPIO8, GPIO9**; USB pins are **GPIO18/GPIO19**.

### 6.2 Optocoupler sense front-end (per channel — repeat for OFF)
PC817 pins: **1 = LED anode, 2 = LED cathode, 3 = emitter, 4 = collector.**
```
FIELD SIDE (GND_OPTO domain)                 LOGIC SIDE (GND_ESP domain)
  +5V_OPTO ── 330Ω ── PC817·1 (anode)        PC817·4 (collector) ── GPIO1  (+10k to 3V3 or use INPUT_PULLUP)
  PC817·2 (cathode) ── meter COM             PC817·3 (emitter)  ── GND_ESP
  meter ON terminal ── GND_OPTO
```
Contact closes → LED lights → phototransistor conducts → GPIO pulled **LOW**. Anode (pin 1) must be on the resistor/+5 V side.

### 6.3 SSR & contactor
Fotek SSR terminals: **3 = DC+, 4 = DC−, 1 & 2 = AC load.**
```
GPIO10 ─────────► SSR-3 (DC+)          [ACTIVE-HIGH: GPIO HIGH = SSR ON]
GND_ESP ────────► SSR-4 (DC−)
Live (coil sup) ► SSR-1
SSR-2 ──────────► contactor coil A1
contactor A2 ───► Neutral (coil sup)
RC snubber ─────► across coil A1–A2
```
> ⚠️ SSR-40DA's rated input minimum is **4 VDC**; the ESP GPIO high is 3.3 V. Many clones still trigger reliably (as deployed here); if yours doesn't, drive the SSR from 5/12 V through a small transistor gated by GPIO10.

### 6.4 Power
- **PS1 (HLK-5M05)** → ESP `5V`/`GND` (`GND_ESP`). Add a **470–1000 µF** bulk cap + 0.1 µF at the board.
- **PS2 (HLK-5M05)** → opto LED side (+5 V_OPTO / `GND_OPTO`), tied only to the meter COMs.
- **Coil supply** → the 500 A contactor's coil circuit, switched by the SSR (`GND_LOAD`).

---

## 7. Repository structure

Exactly as maintained in the repo (Other Resources sorted alphabetically):

```
Latching-Relay-HV-System/
├── Finalized Circuit Diagram.png     ← full schematic
├── main-v6-04-07-2026                ← ★ FINAL deployed firmware
├── README.md                         ← this file
└── Other Resources/                  ← development history (iterations & diagnostics)
    ├── board.py
    ├── Contactor and SSR control code-v3-03-07-2026
    ├── Contactor control-v1-via relay-03-07-2026
    ├── Contactor control-v2-with Watchdog-03-07-2026
    ├── final test-03-07-2026
    ├── final-code-opto-isolated-SSR-04-07-2025
    ├── main-v1-01-07-2026
    ├── main-v1.1-01-07-2026
    ├── main-v2-02-07-2026
    ├── main-v3-02-07-2026
    ├── main-v4-contactor-03-07-2026
    ├── Signal sense code-v1-02-07-2026
    ├── Signal sense code-v2-03-07-2026
    ├── Signal sense code-v3-03-07-2026
    └── Signal sense-code-v4-with optos and reversed polarity-04-07-2026
```

**Key files:**

| File | Description |
|---|---|
| 📐 [**Finalized Circuit Diagram.png**](https://github.com/HumayunNaveedKhan/Latching-Relay-HV-System/blob/main/Finalized%20Circuit%20Diagram.png) | Full schematic: meter → optocouplers → ESP32-C3 → SSR → contactor. |
| ⭐ [**main-v6-04-07-2026**](https://github.com/HumayunNaveedKhan/Latching-Relay-HV-System/blob/main/main-v6-04-07-2026) | The finalized, deployed firmware — flash this. |
| 📄 **Engineering Report** | Full design/iteration report (added alongside this README). |

**Other Resources — development history:**

| File | What it is |
|---|---|
| `board.py` | SKiDL netlist generator for the sense/control PCB (→ KiCad → Gerber). |
| `Contactor and SSR control code-v3` | Transitional SSR-driven output stage. |
| `Contactor control-v1-via relay` | First contactor output stage — electromechanical relay (later found to weld under coil switching). |
| `Contactor control-v2-with Watchdog` | Relay-based stage hardened with a watchdog and state persistence. |
| `final test` | Bench test sketch used during commissioning. |
| `final-code-opto-isolated-SSR` | Earlier finalized opto + SSR build, superseded by `main-v6`. |
| `main-v1` / `main-v1.1` | Initial sensing/logic sketches. |
| `main-v2` / `main-v3` | Signal-investigation revisions. |
| `main-v4-contactor` | First version driving the contactor directly. |
| `Signal sense code-v1` – `v3` | Diagnostic sniffers used to characterize the meter's raw signal. |
| `Signal sense-code-v4-with optos and reversed polarity` | Sniffer updated for opto-isolated sensing with corrected signal polarity. |

> Note: internal photographs of the meter's own PCB are intentionally **not** published here — the meter is a sealed utility device and opening it can constitute tampering; only its external, designated ON/OFF/COM terminals were used.

---

## 8. Quick start

1. **Board support:** Arduino IDE → install *esp32* core → select **ESP32C3 Dev Module** (USB-CDC on Boot: Enabled).
2. **Flash** `main-v6-04-07-2026`.
3. **Wire** per §6 (opto front-end, SSR, three separate grounds).
4. **Serial monitor @ 115200.** On boot it prints the reset reason and the restored state.
5. **Bench test without the meter:** send `1` (ON), `0` (OFF), `t` (toggle).
6. **Live test:** command a real **connect** then **disconnect** from the meter dashboard. The log prints `CONNECT (SSR ON)` / `DISCONNECT (SSR OFF)` and the contactor latches accordingly.

---

## 9. Latching / normal relay feasibility study

Standalone **latching/impulse relays** were evaluated as a no-microcontroller alternative. **Conclusion: not feasible as a drop-in**, for the reasons below.

![Relay units evaluated](img/relay_feasibility_contact_sheet.jpg)

| Device tested | Why it does **not** fit |
|---|---|
| Schneider/Merlin-Gerin **TL / iTL** (A9C30811) | Single-input **toggle** — cannot tell CONNECT from DISCONNECT. 230 VAC/110 VDC coil, not the 12 V/5 V available. |
| Hager **EPN 518** | Single-input toggle, 24 VAC coil. Two switched contacts, but no separate ON/OFF **control** inputs. |
| ABB **E290-16-10/230** | Single-input latch + manual I/O handle; **230 V** coil. |
| AEG **Elfa VFSS 16** | This is a **staircase timer**, not a latch — drops itself after a time; 230 V coil. |

**Root causes:** (1) the job needs **two independent control inputs** (only the Schneider **iTLc**, with dedicated central ON/OFF terminals, has this — not the plain iTL); (2) most stock units are 230 VAC coils vs. the 12 V/5 V on site; (3) the meter's ON/OFF outputs are **low-current, opto-isolated**, while a relay coil pulls 100–300 mA — any relay route still needs the same opto + transistor buffer as the deployed design.

**Conclusion:** an electromechanical route (e.g. a genuine 12 V iTLc, or a two-relay DOL seal-in) is possible in principle but still needs the same isolation front-end, with none of the deployed design's logging, state memory, or firmware safeguards. **The opto + ESP32-C3 + SSR firmware latch is the deployed solution.**

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **ESP hangs; serial dead until unplugged** | EMI latch-up / interrupt storm / brownout from contactor & motor switching | Use the **SSR** (no arc), **opto-isolate** with a **separate supply**, star-ground + **bulk cap** + ferrites; keep the **watchdog** on. Read the boot **reset reason**. |
| **Relay self-toggles every few seconds** | EMI read as signal on bare high-impedance sense lines | Opto-isolation + **sustained-LOW** detection (not single-edge). |
| **`Interrupt wdt timeout`** | Edge-interrupt **storm** from EMI | Switch to **polling** (as deployed). |
| **Contactor won't drop out; connect works** | Hobby relay contacts **welded** by coil inrush arcing | **RC snubber** across the coil **and** the **SSR** (no contacts to weld). |
| **No signal, but a jumper works** | Meter **COM on wrong ground**, opto **LED reversed**, or **meter not firing** | Verify COM → correct ground; anode on +5 V side; jumper **at the meter terminals**; command the **opposite** state. |
| **"Worked yesterday, nothing today"** | A **COM→GND wire popped**, or meter commanded to a state it's already in | Check continuity; force a real ON→OFF. |
| **Boot reason = `BROWNOUT`** | Supply dips when the coil/relay switches | Separate supply, **1000 µF** bulk cap; consider a small UPS/battery on the ESP. |
| **Boot reason = `PANIC`/`WDT`** | EMI hang | Opto isolation, coil snubber, series-R + small cap on sense lines, ferrites. |
| **SSR won't switch from 3.3 V** | SSR-40DA input min is **4 V** | Drive from 5 V/12 V via a transistor. |
| **False triggers still slip through** | Threshold too low | Raise `THRESHOLD_MS` (up to ~2000 ms; real signal ≥3000 ms). |

---

## 11. Safety

- **Mains and 500 A contactor circuitry** must be wired by a qualified person with proper insulation, clearance/creepage, and enclosure.
- **Isolation is the design.** Keep the three grounds separate; keep the coil **RC snubber** fitted; do not bypass the optocouplers.
- **Utility meters are sealed.** Opening a utility-owned meter may break a seal and constitute tampering, and exposes mains and a lithium cell. **Work only from the meter's designated external ON/OFF/COM terminals.**

---

## 12. System range, capabilities & limitations

**Capabilities**
- Controls a contactor coil of **any current rating** — the SSR switches only the low-current coil, never the load path, so the same electronics scale from small contactors up to 500 A (verified) and beyond, limited only by coil VA/inrush vs. SSR and snubber sizing.
- Detects and latches on the meter's real signal with **~800 ms response**, against a ≥3 s real signal — wide margin.
- **Self-recovering**: watchdog auto-resets on any hang (≤8 s) and the last state is restored from flash — no manual intervention needed after a transient fault.
- **Diagnosable in the field**: boot-time reset-reason reporting distinguishes supply issues from EMI issues.
- Serial override (`1`/`0`/`t`) for commissioning and testing without the meter.
- Fully isolated (3 independent grounds) — safe to install alongside heavy inductive loads (motors, ACs) without re-engineering.

**Limitations**
- Detection thresholds are tuned to **this meter's measured pulse/duration profile**; a different meter model, or a firmware update to this meter, could change timing and require re-tuning `THRESHOLD_MS`.
- The system reflects the meter's **commanded** state — it cannot make the meter's own motorized breaker actuate. Occasional missed first-presses (requiring a second dashboard command) are meter-side, outside this system's control.
- The SSR-40DA's rated control-voltage minimum (4 V) is technically above the ESP's 3.3 V logic-high; the deployed unit triggers reliably, but a 5–12 V driver stage is recommended for full margin on future builds.
- No auxiliary feedback contact is wired back from the contactor itself; the system confirms it *commanded* a state, not that the contactor's main contacts physically followed (relevant mainly if a contactor were to mechanically fail or weld).
- This system is a **control follower only** — it does not provide load metering, overcurrent, or short-circuit protection; upstream breakers/fuses remain the site's primary protection.
- Wi-Fi/BLE on the ESP32-C3 is unused in the deployed firmware; adding wireless features later should be re-validated for timing margins against this document's thresholds.

---

## 13. Other projects by the author

- 🤖 [**Generalized Haptic Robotic Teleoperation Setup (GTS)**](https://github.com/HumayunNaveedKhan/Generalized-Haptic-Robotic-Teleoperation-Setup-GTS)
- 💧 [**Smart Industrial Liquid Level Monitoring Device (NED-SILL)**](https://github.com/HumayunNaveedKhan/Smart-Industrial-Liquid-Level-Monitoring-Device-NED_SILL)

---

### Credits
Design, development, firmware and documentation by **Humayun Naveed Khan (R&D Engineer)** for **Thal Engineering**.
