# FireShield Design System: The Editorial Finishing Room

**Version**: 1.0  
**Application**: FireShield Web Operator Console & Editorial Suite  
**Target Viewports**: 1440px (Desktop), 1366px (Laptop), 390px (Mobile Inspection)  

---

## 1. Aesthetic Philosophy: "The Editorial Finishing Room"

FireShield's visual language is modeled after a professional film color-grading and broadcast quality-control suite. Standards and clearances editors spend hours inspecting high-frequency visual anomalies. The design strictly rejects generic neon/cyberpunk tropes:

* **NO** glowing cyan grids, matrix rain, gratuitous purple gradients, or rounded toy buttons.
* **YES** quiet, deep graphite surfaces that preserve the operator's contrast sensitivity.
* **YES** warm ivory / parchment inspection panels that evoke printed legal clearance certificates.
* **YES** muted aged bronze / brass accents (`#c89b3c`) indicating cryptographic authority and verified approval.
* **YES** strict tabular numerals (`font-variant-numeric: tabular-nums`) for high-precision timecodes, frame counters, and cryptographic hex digests.

---

## 2. Color Palette & Semantic Tokens

```css
:root {
  /* Canvas & Stage (Dark Neutral Graphite) */
  --stage-bg: #0e1117;
  --stage-surface: #141822;
  --stage-panel: #1b212d;
  --stage-border: #262e3e;
  --stage-border-subtle: #1e2532;

  /* Editorial Inspector (Warm Ivory & Parchment Accents) */
  --ivory-card: #191f2b;
  --ivory-card-light: #fbf9f4;
  --ivory-border: #333d4e;
  --ivory-text: #f1f4f8;
  --ivory-muted: #8d98aa;

  /* Typography */
  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-tertiary: #64748b;
  --text-ink: #0f172a;

  /* Authority & Verification (Muted Bronze / Aged Brass) */
  --bronze-primary: #c89b3c;
  --bronze-hover: #dfa837;
  --bronze-dark: #8e6822;
  --bronze-subtle: rgba(200, 155, 60, 0.12);
  --bronze-border: rgba(200, 155, 60, 0.35);

  /* Status Tokens */
  --status-clean: #10b981;
  --status-clean-subtle: rgba(16, 185, 129, 0.12);
  --status-clean-border: rgba(16, 185, 129, 0.3);

  --status-hazard: #f59e0b;
  --status-hazard-subtle: rgba(245, 158, 11, 0.14);
  --status-hazard-border: rgba(245, 158, 11, 0.35);

  --status-rejected: #ef4444;
  --status-rejected-subtle: rgba(239, 68, 68, 0.14);
  --status-rejected-border: rgba(239, 68, 68, 0.35);

  --status-published: #38bdf8;
  --status-published-subtle: rgba(56, 189, 248, 0.12);
  --status-published-border: rgba(56, 189, 248, 0.3);

  /* Interactive Timeline Tracks */
  --timeline-bg: #090b10;
  --timeline-track: #151a24;
  --timeline-ruler: #202735;
  --timeline-playhead: #f59e0b;
  --timeline-hazard-lum: #d97706;
  --timeline-hazard-pattern: #8b5cf6;
  --timeline-safe-margin: repeating-linear-gradient(45deg, #1f2937, #1f2937 4px, #374151 4px, #374151 8px);
  --timeline-subtitles: #2563eb;
}
```

---

## 3. Typography Specification

```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Manrope", Roboto, "Helvetica Neue", sans-serif;
font-feature-settings: "cv02", "cv03", "cv04", "cv11";
```
* **Tabular Data**: `.tabular-nums { font-variant-numeric: tabular-nums; font-family: "JetBrains Mono", "SF Mono", Consolas, monospace; }`
* **Hierarchy**:
  * Top Bar Brand: 15px / 600 weight / tracking 0.08em uppercase.
  * Section Titles: 13px / 600 weight / tracking 0.04em uppercase.
  * Timecode Counter: 24px / 700 weight / tabular-nums.
  * Body Text: 13px / 400 weight / line-height 1.5.
  * Captions & Metadata: 11px / 500 weight / uppercase tracking 0.05em.

---

## 4. Layout & Grid Architecture

### 1440px Desktop Workbench
* **Top Navigation Bar** (Height: 56px, Sticky)
  * Left: Brand mark ("FIRESHIELD · S&P CLEARANCE SUITE") + Active tenant indicator.
  * Center: Tab switchers ("Screening & Clearance", "FireTrace Incident Lab", "Ed25519 Sidecar Audit").
  * Right: Offline Engine Health Pill + TV Hardware Gateway status badge.
* **3-Column Workspace** (Height: Calc(100vh - 56px))
  * **Left Rail (Catalog & Ingest)**: 280px fixed width.
  * **Center Stage (Inspection & Visual Timeline)**: Flex-1 minimum 640px.
    * Video/Frame inspection canvas (480x270 aspect ratio with playhead controls).
    * Multi-track interactive timeline (Ruler, Luminance waveform proxy, Hazard blocks, Subtitle track, Scrubbable playhead cursor).
  * **Right Inspector (Editorial Gate & Sidecar Publisher)**: 380px fixed width.
    * Tabbed inspector: Hazard Metrics | Synaptic Shield Context | Human Review Gate | Ed25519 Publisher.

### 1366px Laptop Mode
* Rail collapses to 240px; inspector scales to 340px; stage adapts with fluid responsive SVG timeline.

### 390px Mobile Inspection
* Single column stacked layout.
* Sticky bottom tab bar for fast toggling between Catalog, Stage, Inspector, and FireTrace incidents.
