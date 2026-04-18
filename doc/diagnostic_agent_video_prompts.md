# Diagnostic Agent — Mock Visuals & Video Prompts (English)

This doc packages the visual mock + 9-stage video generation prompts in English,
mirroring the Chinese brief for the self-diagnostic agent demo.

---

## Part A — Static Mock (English Version)

### Global Visual Settings

- **View:** Strict front / orthographic view. Completely flat, no perspective, no 3D rotation.
- **Style:** Futuristic FUI (Futuristic User Interface), HUD infographic aesthetic, flat vector illustration.
- **Composition:** Mirror-symmetric left/right layout. A vertical chatbot panel sits dead-center; thin **leader lines** extend left and right to floating detail panels.
- **Background:** Pure white, no shadows, crisp and clean.
- **Colors:**
  - Primary: **Tech Blue**
  - **API** accents: Vivid Green
  - **SQL** accents: Chartreuse (yellow-green)
  - **Code** accents: Cyan
  - **LLM** accents: Amber Gold / Orange — the hero highlight color

### 1. Central Subject — The Chatbot Panel

- **Form factor:** Vertical rectangle, phone-screen proportions (≈ 1:4), with an ultra-thin blue border.
- **Title bar:** Top reads **"Autonomous Diagnostic Agent"**.
- **Bottom input complex (detailed):**
  - **Input text:** `"Diagnose packet loss on element N105, Liuxiandong, Xili, Nanshan District"`.
  - **Smart suggestion:** A translucent rectangular dropdown floats above the token `N105`; first row highlighted `"N105 — 5G base station (Xili cluster)"`, followed by two dim greyed rows.
  - **Scene menu (+ menu):** The `+` icon on the left of the input is expanded, fanning a 2×2 or 3×2 grid of colored flat vector icons to the upper-left: **stethoscope (diagnose)**, **wrench (ops)**, **magnifier (query)**, and two more tool glyphs.
- **Central workflow:** Vertically stacked rounded-card nodes connected by downward arrows.
- **Loop block:** A deep-blue **dashed rectangle** wraps the middle nodes `Text2SQL` and `Text2Code`, labeled `Loop × N` on the side.

### 2. Left Side — Mechanism Breakdown

- **Visual logic:** Thin capped leader lines extend horizontally left from the center nodes.
- **Text2SQL breakdown:**
  - Source: center node `"Collect element port performance data"`.
  - Expanded content: a flat **ER diagram** with two rectangular tables `Table: Port` and `Table: Alarm`, fields visible, joined by a thin line.
  - Above the tables a monospaced code line: `SELECT * FROM Port WHERE ID = N105`.
- **Text2API breakdown:**
  - Source: center node `"Query alarming element"`.
  - Expanded content: a left-growing **horizontal tree**; root labeled `API`, three children, one child highlighted.

### 3. Right Side — Thinking & Results

- **Visual logic:** Thin leader lines extend horizontally right from the center nodes.
- **LLM Chain of Thought:**
  - Source: center node `"LLM reasoning"` (rendered in amber gold).
  - Expanded content: **three vertically stacked chat bubbles**:
    1. `"Analyzing optical path telemetry…"`
    2. `"Ruling out hardware fault…"`
    3. (highlighted) `"Likely congestion — confidence 85%"`
  - A tiny flat **brain icon** next to the bubbles.
- **Text2SQL result panel:**
  - Source: center node `"Data collection"`.
  - Expanded content: a flat **mini-chart panel** showing a red line chart with a sharp drop, beside a numeric readout `Packets: 0/s`.

### Instructions for Output Variation (sequential)

1. **Image 1 — Concept, WITH text.** High-fidelity flat vector illustration, pure white background, futuristic FUI. Include every English label above. Exact typography is flexible; keep it crisp and sans-serif.
2. **Image 2 — Clean Base, NO text.** Same layout, same icons, same colors, **REMOVE ALL TEXT**. Leave empty placeholders inside every card, input box, popover, table, tree node, bubble, and chart axis.
3. **Image 3 — Icon pack (separated).** Isolate just the glyphs used: **database table**, **brain**, **horizontal tree**, and the **+ menu icon grid**. Arrange them on a white background, evenly spaced, consistent stroke weight, consistent corner radius.

---

## Part B — 9-Stage Video Prompts

Shared constants for each stage:

```
Camera: Front orthographic view, locked, no push-in, no rotation.
Frame: Pure white background, mirror-symmetric composition.
Style: Flat vector, futuristic FUI, HUD infographic, thin leader lines.
Palette: Tech blue primary, amber gold for LLM emphasis,
         vivid green for API, chartreuse for SQL, cyan for Code.
Pace: Steady motion, smooth tweening, 24–30 fps feel.
Rendering hint: Crisp 1px strokes, no drop shadows, no perspective distortion.
```

Each stage is about **5 – 8 seconds**; concatenated total ≈ **60 seconds**.

---

### Stage 1 — User types the question (≈ 5 s)

> Flat-vector FUI interface, front orthographic. A slim vertical chatbot panel sits center on pure white. Camera holds still. The bottom input field pulses softly; characters of the Chinese-equivalent diagnostic query `"Diagnose packet loss on element N105, Liuxiandong, Xili, Nanshan"` type in left-to-right, one glyph at a time. Over the token `N105` a translucent suggestion popover rises into view with its top row highlighted `"N105 — 5G base station (Xili cluster)"`. The `+` icon on the left glows tech blue. Leader-line panels on both sides remain dim and dotted. A soft blue scanline wipes once top-to-bottom to suggest UI readiness.

**Key motion:** typing animation · suggestion popover slides in · tool `+` button pulses · no camera move.

---

### Stage 2 — Diagnostic starts, workflow lights up (≈ 7 s)

> User presses Enter (implicit). The input bar contracts into the central workflow column. One after another, rounded workflow cards light up top-to-bottom with a tech-blue flash: `Receive Query → Plan → Collect Port Data → Query Alarms → LLM Reasoning`. Simultaneously, leader lines extend from the center nodes outward on both sides, drawing the left-hand ER diagram (`Table: Port`, `Table: Alarm` with a `SELECT * FROM Port WHERE ID = N105` ribbon) and the right-hand three chain-of-thought bubbles (`"Analyzing optical path"`, `"Ruling out hardware"`, amber-highlighted `"Likely congestion…"`). A dashed deep-blue rectangle wraps `Text2SQL` + `Text2Code` with a small `Loop × N` tag animating in. A thin red line chart appears on the right (`Packets: 0/s`). The LLM node glows amber gold, subtly breathing.

**Key motion:** cards light sequentially · leader lines draw out · ER diagram and CoT bubbles populate · LLM node breathes amber · loop rectangle animates.

---

### Stage 3 — User interrupts mid-diagnosis (≈ 5 s)

> Mid-stream. The workflow is still animating: the `Loop × N` rectangle is pulsing, the red line chart is scrolling. Suddenly the input field at the bottom re-activates — a cursor blinks — and new text types in: `"Stop. Check optical-power anomaly first."`. The whole center column briefly freezes: the current active card desaturates to grey, the loop rectangle pauses, the amber LLM node dims from gold to muted ochre. A thin red `INTERRUPT` banner streaks across the workflow from left to right for half a second, then fades.

**Key motion:** user input re-emerges · workflow freezes and desaturates · red interrupt banner wipes across · LLM amber dims.

---

### Stage 4 — Diagnostic re-routes to the user's new intent (≈ 7 s)

> The frozen workflow rearranges: the old `Collect Port Data → Query Alarms` nodes fade back, and a fresh branch slides in labeled `"Check Optical Power Anomaly"`. The leader lines on the left retract their ER diagram and redraw a new one showing a table `Table: OpticalPort` with a `SELECT power FROM OpticalPort WHERE id = N105` ribbon. On the right, three new CoT bubbles replace the old ones: `"Sampling optical power window"`, `"Detecting excursion"`, amber-highlighted `"Anomaly at −28 dBm"`. The amber LLM node re-ignites. Cards re-flash tech blue one after another. A subtle check-mark pulse confirms the new plan is accepted.

**Key motion:** old nodes retract · new optical-power branch slides in · ER diagram and CoT redraw · LLM re-ignites amber.

---

### Stage 5 — Diagnosis finishes, user adds a new direction (≈ 6 s)

> The workflow concludes: the final card `"Conclusion"` lights up in amber gold and a result card glides down from it, reading `"Root cause: optical-power anomaly (−28 dBm)"`. The right-hand mini-chart updates: the red line steadies back to baseline. The user's input field re-awakens and types: `"Also verify upstream fiber attenuation."` A slender amber chevron arrow extends from this new instruction into the workflow, indicating branching. Cards wait in a "ready" pose (slight vertical bounce) anticipating the new step.

**Key motion:** result card glides down · mini-chart stabilizes · user types add-on · amber chevron branches into workflow.

---

### Stage 6 — New user-provided diagnostic step is inserted mid-flow (≈ 7 s)

> At the tail of the existing workflow, a new rounded card slides in from the right, labeled `"Fiber Attenuation Test"`, bordered in amber gold to mark it as user-contributed. Tech-blue leader lines extend from this new card: on the left, a fresh `SELECT attenuation FROM FiberLink WHERE endpoint = N105` ribbon plus a stylized waveform table; on the right, an additional CoT bubble `"Scanning attenuation curve"` and a second mini-chart with a cyan waveform. The workflow then re-enters motion, processing from this insertion point downward, cards flashing again in sequence.

**Key motion:** amber-bordered new card slides in · new SQL panel draws on left · new waveform chart draws on right · workflow resumes from insertion point.

---

### Stage 7 — Diagnostic completes, user-added step shows its own result (≈ 6 s)

> All cards settle. The amber-bordered user step completes with its own small `"+2.4 dB deviation at 3.1 km"` result pill attached beneath it (in amber). The main conclusion card at the bottom expands into a split layout: a left column headed `"System-derived causes"` listing `"Optical-power anomaly"`; a right column headed `"User-supplied finding"` listing `"Fiber attenuation deviation +2.4 dB"`. Both columns get small check marks. The right-hand result panel now shows two coexisting mini-charts stacked.

**Key motion:** amber result pill pops in under new card · main conclusion splits into two columns · two stacked mini-charts appear.

---

### Stage 8 — User confirms the true root causes (≈ 5 s)

> A translucent selection cursor (tech blue) appears in the conclusion card and ticks the two items: `"Optical-power anomaly"` and `"Fiber attenuation deviation"`. Both selected lines illuminate with a soft amber outline. Below, an affordance button labeled `"CONFIRM ROOT CAUSE"` glows blue. The user clicks — it ripples once and turns amber. The other candidate lines desaturate to grey, emphasizing the selection.

**Key motion:** cursor taps two lines · selected lines glow amber · CONFIRM button ripples · non-selected items desaturate.

---

### Stage 9 — Root cause output + experience-library ingestion (≈ 7 s)

> The conclusion card floats slightly upward and compacts into a result badge reading `"ROOT CAUSE · Optical-power anomaly + Fiber attenuation deviation"` in amber gold on white with a thin amber border. A new data-flow line extends from the badge diagonally outward to a previously unseen side panel that fades in from the right edge, labeled `"EXPERIENCE LIBRARY"`. A small bundle icon (folder + star) travels along the flow line into the library, where it is filed. A confirmation pill lights up `"Case archived — ID exp-0042"`. The screen then fades to a neutral standby with every card dimmed except the amber badge and the library panel, which stay lit.

**Key motion:** result badge compacts · flow line draws to experience-library panel (slides in from right) · packet icon travels along flow · `"exp-0042"` confirmation pill appears · screen fades to standby with amber + library highlighted.

---

## Part C — Prompt-Format Notes for Common Video Generators

- **Runway Gen-3 / Pika:** keep each stage to one paragraph, put camera direction at the end, repeat palette tokens in the description so color doesn't drift between cuts.
- **Veo / Sora:** you can concatenate stages with explicit timecodes, e.g. `00:00–00:05 — Stage 1 …; 00:05–00:12 — Stage 2 …`; generators tend to honor locked-camera requests better when phrased as `"camera locked, no movement, orthographic front view"`.
- **Keep UI labels as English text**; Chinese glyphs in generated video often render with artifact noise. If you must include Chinese, render static frames with HTML/SVG and composite them over the generated motion.
- **Consistency tokens to reuse every stage:** `flat vector`, `futuristic FUI`, `pure white background`, `tech blue primary`, `amber gold LLM`, `thin leader lines`, `front orthographic`, `no perspective`.
