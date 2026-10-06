# Synaptic Shield | Hardened Idea File

**Fire TV track · Backend and product thesis · 28 September 2026**  
**Status:** Research-backed design hypothesis, not a validated medical device or a working integration.

## 1. Executive snapshot

Synaptic Shield is a **version-bound sensory intervention layer for streaming publishers**. A publisher analyzes each authorized video rendition before release, reviews flagged intervals, and publishes a signed hazard timeline tied to that exact media version. Its own Fire TV player checks the timeline before playback reaches a flagged interval and can pause, bypass it, or use a publisher-approved alternative rendition; a short, grounded context note can preserve the story when the viewer skips. The first buyer is a small or mid-sized streaming service with a Fire TV app and a catalog of high-intensity or creator-supplied titles. The defensible problem is **reliable intervention at the point of playback across edits, streams, and ad insertions**, rather than detecting flashes or inventing a systemwide Fire TV filter.

**Hard correction to the attached proposal:** A normal Fire TV app cannot be presumed to inspect and modify frames in Prime Video, Netflix, or other apps. Fire TV/Vega secure playback and documented WebGL restrictions invalidate that central promise. This design operates only on content and playback the publisher authorizes and controls. On Vega, video uses private GPU buffers that prevent extracting frames as WebGL textures; older Fire TV web-app guidance says WebGL is unsupported on Fire TV Stick. Device and OS verification remains mandatory. [A1–A4]

## 2. Problem validation and precise wedge

- **The hazard is real:** The Epilepsy Foundation says visual triggers affect about **3% of people with epilepsy**, while W3C's WCAG 2.3.1 addresses general and red flashes. This is not a claim that 3% of all viewers have photosensitive epilepsy. The proposal's “1 in 4,000 globally,” broad autism/migraine prevalence, and specific HDR formula should not enter the pitch without primary support and clinical review. [P1–P3]
- **Warnings do not solve timing:** HBO Max documents title/player photosensitivity warnings. CineGuard publishes moderated scene timestamps but warns that different cuts and intros cause drift. This supports a narrower need: a warning attached to a title is less actionable than a correct, version-specific intervention point. [P4–P5]
- **Analysis tools already exist:** EA IRIS finds luminance flashes, red flashes, and patterns; HardingFPA offers broadcast-oriented analysis. A new luminance detector alone is not a wedge. [C1–C2]
- **Prior hackathon work overlaps heavily:** Caesar already blocked and offered skip/proceed; Epilepsy Safe Viewer applied an advance filter; Flikcer identified timestamps and produced altered videos. “Detect → pause → skip” and “filter → preserve viewing” are not novel claims. [C3–C5]
- **Unvalidated business pain:** We have evidence of viewer need and current products, but **no measured publisher budget, legal requirement to buy this workflow, or signed design partner**. Treat purchase intent and ROI as hypotheses. The most valuable next research is interviews with streaming playback and accessibility owners, not another prevalence estimate.

**Target buyer:** Director of Accessibility or VP of Product at a niche streaming publisher, with a playback engineering lead as technical sponsor and content operations as daily owner. Initial segment: services with licensed, owned, or creator-supplied video and a Fire TV app. Amazon/Prime Video integration is a possible long-term partnership, never an assumed route to market.

**Job to be done:** Release a catalog that has a reviewable visual-hazard timeline and allow a viewer to cross an affected scene without a last-second scramble, while keeping playback behavior correct after edits and stream switches.

## 3. Solution and backend architecture

### Plain-language contract

The publisher uploads or authorizes a source asset. A screening job proposes hazardous intervals, a human reviews high-impact decisions, and a release gate binds approved intervals to the actual published rendition. The Fire TV app fetches only the timeline matching the video it is playing. Before an interval begins, the player applies an explicit policy and records whether it intervened. **No matching verified timeline means “unreviewed,” not “safe.”**

### Service boundaries

1. **Ingest and identity:** S3 receives authorized media and optional captions/transcripts. Calculate a content hash and immutable `asset_revision_id`; keep rights and region metadata. The screening queue is idempotent on revision and detector version.
2. **Analysis and review:** A batch worker runs a deterministic flash/red/pattern analyzer on the mezzanine file or decoded test rendition. EA IRIS is a candidate baseline, subject to license/build review and independent validation. Normalize findings to source frame indices and confidence/criteria; route ambiguous cases to human review. This is screening, **not Harding certification**.
3. **Context and alternative preparation:** When a bypass would lose narrative information, derive a short text note from publisher-owned subtitle/transcript lines bracketing the interval. Amazon Bedrock/Nova may draft it, but require grounded citations to source cue IDs and human approval for publication. Alternative video/audio renditions require publisher rights, editorial approval, and their own hazard scan. No live model call is on the safety-critical playback path.
4. **Package and publish:** MediaConvert creates playback renditions. A mapping job translates source intervals to each output's presentation timeline and checks segment boundaries, edits, intro offsets, frame-rate changes, and ad policy. Publish a **signed, versioned sidecar manifest** atomically with the rendition. A manifest is rejected when hash/revision/region/rendition mapping fails; do not silently reuse timestamps. The sidecar approach is a proposed implementation, not a claimed Fire TV platform feature. [A5–A6]
5. **Playback policy engine:** The publisher's Fire OS/Vega app reads player position and a prefetched sidecar. A deterministic state machine (`UNREVIEWED → ARMED → INTERVENING → RESOLVED`, plus `ERROR`) schedules a guard **before** the first at-risk frame. On seek into an interval, restart, ad return, rate change, or rendition switch, re-evaluate. Default response for known high-severity intervals is pre-emptive pause or licensed safe alternate; automatic skip is configurable by explicit user preference. Audio-only behavior is permitted only if player, licensing, and a verified safe visual surface support it. No “managed clamp” claim until measured on target hardware and reviewed for safety.
6. **Evidence and telemetry:** Store per-asset detector version, human decision, timestamp mapping proof, publishing revision, and policy outcome. Collect coarse operational events, not diagnoses or raw viewer frames. Encrypt, minimize retention, and separate tenant data. An operator can revoke a timeline immediately; clients fail closed for *known* hazard metadata when verification fails, while clearly labeling an unseen asset as unreviewed.

### Minimal release contract (illustrative, proposed)

```json
{
  "schema_version": 1,
  "tenant_id": "publisher-01",
  "asset_revision_id": "sha256:...",
  "rendition_set_id": "release-2026-09-28-v2",
  "timebase": "presentation_ms",
  "detector_version": "iris-or-validated-engine:version",
  "review_state": "approved",
  "intervals": [{
    "start_ms": 42000, "end_ms": 51000,
    "types": ["luminance_flash"],
    "policy": "pause_or_skip",
    "safe_resume_ms": 51500,
    "context_cue_ids": ["cue-184", "cue-185"]
  }],
  "manifest_digest": "sha256:...",
  "signature": "publisher-signature"
}
```

`safe_resume_ms` is reviewed with a margin after the final flagged frame, not inferred by a language model. The client must verify revision, signature, schema, monotonic non-overlapping intervals, and supported policy before arming. An “approved” label means editorial approval of this timeline, not a guarantee of seizure prevention or regulatory certification.

### Technical gates before any safety claim

- **Playback feasibility:** On a Fire TV/Vega simulator and a physical target where available, show accurate pre-event pause and seeking for owned HLS media, including segment boundary, seek into hazard, resume, app suspend, and a changed rendition.
- **Detection quality:** Compare against known positive/negative test media and, if licensed, a recognized specialist tool. Report false negatives, false positives, pattern/red cases, timing error, and performance; do not tune on a single stylized clip.
- **Release alignment:** Edit intro, alter segment duration, re-encode, and inject a controlled ad break. Old sidecars must be rejected; output timelines must align with actual displayed frames.
- **Policy safety:** Unavailable sidecar, network outage, stale cache, unreviewed content, and failed seek each have an explicit observable outcome. Never claim a dark overlay or late dimming makes an already displayed flash safe.

## 4. Amazon-native technology map

| Component | Specific role | Why here / boundary |
|---|---|---|
| Fire OS or Vega OS Fire TV application + platform media player | First-class TV playback, remote control, precise player state/seek, locally enforced intervention | **Required track surface** and the only app playback this product can control. Fire TV rules require an app running on Fire OS/Vega OS and a device/simulator video. Use actual player APIs after target selection; not an assumed system overlay. [A1, A7, H1] |
| Amazon S3 | Authorized source, analyzed artifacts, immutable sidecars | Durable, versioned media/object origin; isolate tenants and revisions. [A6] |
| AWS Step Functions + Lambda/EventBridge | Orchestrate ingest, analysis, editorial state, transcode, publish, retry | AWS's published VOD architecture uses these patterns; long frame-analysis jobs need a suitable container/compute worker rather than a short Lambda. [A6] |
| AWS Elemental MediaConvert | Package controlled VOD renditions with identifiable output timelines | Bridges ingest to exact published video. Timeline transforms are a critical product mechanism. [A5] |
| Amazon CloudFront | Serve media and signed sidecar near viewers | Distribution path for a partner-owned catalog; latency and cache invalidation need measurement. [A6] |
| DynamoDB | Version/policy lookup, review state and idempotency | Low-latency release metadata. Do not store “clinical thresholds” or pretend personalized medical calibration. |
| Amazon Bedrock with Amazon Nova Lite or currently available model | Offline draft of a short, source-grounded context note | Optional assistive feature; hard validation and approval; **never** decides hazard boundaries. Exact model/region/access need confirmation. [A8] |
| CloudWatch/KMS/IAM | Operations, key control, least privilege | Tenant security and auditable failures; not a substitute for clinical validation. |

**No Alexa dependency in the first version.** Adding a voice command without a tested need weakens the causal story. AWS services are operationally useful, but other clouds can implement similar pipelines; the honest Amazon-native point is Fire TV playback tied to AWS media lifecycle, not an exclusive algorithmic capability.

## 5. Differentiation matrix and competitive intelligence

Sources were checked on 28 September 2026. “Gap” describes a capability **not established in the cited source**, not proof that a competitor cannot do it. Repository activity status was not established from the available GitHub connector metadata; do not label projects abandoned or actively maintained without commit evidence.

| Competitor / prior art | What it does and stack | Gap / limitation from cited source | Synaptic Shield's proposed difference | Source |
|---|---|---|---|---|
| EA IRIS | C++ cross-platform analysis of luminance, red flashes and patterns; CSV/JSON analysis output | An analyzer, not a publisher playback intervention or revision-bound release controller; explicitly not certification | Reuse/benchmark detector; bind findings to signed Fire TV playback policy and editorial record | [C1] |
| HardingFPA | Commercial analysis and broadcast guideline testing suite | Production assessment, not a player behavior layer | Integrate with existing QA instead of claiming to replace it | [C2] |
| Caesar (HackTX 2019 winner) | JavaScript Chrome extension, MongoDB; blocks screen and offers skip/proceed | Browser/video-tag scope; no evidenced controlled Fire TV delivery/version contract | Version-bound intervention in a publisher-owned Fire TV app | [C3] |
| Epilepsy Safe Viewer | Chrome extension, Flask/OpenCV/Azure; anticipatory warning/filter | YouTube/browser workflow; no evidenced rendition/ad timeline contract | Prepublication review and exact media revision enforcement | [C4] |
| Flikcer | Web tool finds timestamps and produces altered output | Separate test/download workflow; no evidenced native Fire TV publisher operation | Release gate and in-player intervention tied to publish revision | [C5] |
| EPI-LENS | Vanilla JS/GitHub Pages and browser extension; live/local video metrics and exports | Research/audit focus; no documented partner playback enforcement | Publisher-controlled intervention and operational evidence | [C6] |
| CineGuard | Human-moderated timecoded content warnings | Site admits timestamp drift across cuts/regions | Hash/revision and rendition-specific timing contract | [P5] |
| HBO Max warning feature | Photosensitivity warning on title details and beginning of playback | Documented warning, no documented timed scene bypass | Pre-event, specific interval handling (requires partner app) | [P4] |
| Described (current Fire TV event) | Fire TV audio-description pipeline using Nova, Polly, HLS | Addresses visual description, not visual hazard boundary/policy; context-generation area overlaps | Distinguish with verified hazard/revision enforcement, avoid generic “AI scene summary” pitch | [C7] |

**Search boundary:** No verified public Fire TV app combining all of the proposed release contract, hazard review, and playback enforcement was found in this targeted pass. This is **not** proof of global uniqueness. A second pass should inspect more of the current event gallery and commercial publisher tooling. The original proposal's “FlickerFence” and “SceneBridgeTrack” were presented as components, but matching public GitHub repositories were not found in the connector's name/description search; treat their provenance and implementation as unverified.

## 6. B2B value case and battle test

**Current spend or workflow:** Accessibility/content-operations staff, outsourced or commercial flash testing, postproduction revision handling, in-app playback engineering, and customer-support response to complaints. HardingFPA and EA IRIS show that screening is a real category; no verified price or purchasing cadence is asserted. The product complements a certification process, replacing manual timestamp spreadsheets and ad hoc player exceptions only if a buyer confirms those practices. [C1–C2]

**Commercial proposal (hypothesis):** Annual platform fee per streaming publisher plus usage-based analyzed media hours; pilot charge based on a small catalog. Avoid per-viewer medical profiling. Procurement case is reduced release rework and a better supported accessibility experience. For a buyer with `N` releases/year, current manual handling `h` hours/release at loaded cost `w`, the addressable labor baseline is `N × h × w`; add any separately documented rework cost, subtract product and review cost. **All variables require buyer-supplied data.** There is no demonstrated ROI yet.

**Distribution compounding:** One approved hazard sidecar serves many views without inference per viewer. Publisher integrations, versioned review data, and a growing verified title catalog lower marginal serving cost; network effects across publishers need shared rights and are not presumed. The potential moat is operational trust (validated mapping, review history, integration, and measured low false-negative rate), not a patent claim or exclusive algorithm.

### Original proposal score, mutation, and honest rescore

Scores are reviewer judgment out of 10, **not measured performance**. “Rescore” assesses the design thesis if the stated gate is met, not the present codebase.

| Axis | Original | Sharpest weakness | Concrete mutation | Hardened thesis |
|---|---:|---|---|---:|
| Novelty | 3 | Prior art already detects, filters, warns and skips | Make release-version-to-playback verification the central invention | 7 |
| Problem severity | 7 | Real viewer harm, but publisher workflow unmeasured | Interview buyers and measure timestamp drift/rework | 7 |
| Enterprise fit | 4 | “Amazon acquisition” is not a customer strategy | Sell to independent Fire TV publishers with an integration contract | 7 |
| Technical defensibility | 3 | WebGL filter is cloneable and may not run | Build auditable lineage, human review, rendition/ad alignment | 7 |
| Amazon-native depth | 4 | AWS nouns are interchangeable; video buffer access invalid | Enforce policy inside working Fire TV player with AWS VOD pipeline | 7 |
| Scalable distribution | 5 | Per-frame live analysis would cost and fail across apps | Analyze once per revision, cache signed sidecars across plays | 8 |
| Judge/VC wow | 5 | “AI epilepsy filter” sounds familiar and unproven | Show a revision-aware intervention that rejects a stale timeline | 8 |

### Three variants tested against each other

| Variant | Core bet | Novelty / buyer / feasibility / Amazon fit (10 each) | Why it wins or loses |
|---|---|---|---|
| A. Universal ambient shield | On-device screen capture and shader clamp across apps | 3 / 3 / 1 / 2 | Most dramatic claim, blocked by app isolation, DRM/secure buffers and device limits. **Reject.** |
| B. Publisher safety release gate | Offline analysis + editorial approval + revision/rendition identity | 7 / 8 / 8 / 6 | Strongest initial B2B budget story and tractable backend; alone risks becoming a QA report. |
| C. Fire TV intervention runtime | Player state machine + version-matched sidecar + context continuity | 8 / 7 / 6 / 9 | Strongest track fit; requires a publisher-owned player and accurate presentation timeline. |

**Winner: B + C.** The backend release gate has a real operational buyer; the Fire TV runtime gives it user impact. Keep optional AI context from the original, but remove AI from the hazard decision. **What changed:** no systemwide interception; no 60 FPS OffscreenCanvas premise; no clinical threshold personalization; no unsupported WebGL clamping promise; no claim that skipping/context is unprecedented. The center of gravity is verified *asset identity → review → playback enforcement*.

## 7. Real risk register

| Risk | Category | Likelihood | Impact | Mitigation / acceptance gate |
|---|---|---|---|---|
| Cannot inspect or modify other apps' protected video | technical / Amazon-policy | High | Critical | Limit integration to publisher-owned player and licensed media; reject universal-device claim. [A2–A4] |
| Missed hazard or late intervention | technical / compliance | Medium | Critical | Independent benchmark, guard margin, frame-alignment tests, high-risk human review; no safety guarantee. |
| Re-encodes, regional edits or SSAI shift timestamps | technical | High | Critical | Immutable rendition identity, mapping verification, invalidate on changes; partner controls ad path or excludes unreviewed ads. [P5] |
| Summary fabricates or spoils story | execution | Medium | Medium | Transcript-cue grounding, schema/length restrictions, editorial approval; suppress if unsupported. |
| Detection is not broadcast/medical certification | compliance | High | High | Describe as screening and intervention support; retain recognized QA pathway; legal/clinical review before claims. [C1–C2] |
| Licensing prohibits derivative/rerendered stream or audio-only mode | compliance | Medium | High | Contract check; avoid alternative rendition until rights confirmed. |
| Fire OS/Vega device-specific seek or buffer behavior | technical | Medium | High | Simulator plus target-device tests, measured lead time, model-specific capability flags. [A7] |
| No buyer budget or willingness to integrate SDK | market | Medium | High | 8–12 exploratory interviews, 2 integration prospects, written pilot criteria before platform expansion. |
| Tenant or viewer sensitivity leaks | compliance / technical | Medium | High | Minimal metadata, no diagnosis by default, encryption, access controls, short telemetry retention. |
| Cloud compute and human review scale poorly | market / execution | Medium | Medium | Analyze once per revision, severity triage, cost per media hour and review-minute targets measured in pilot. |
| Ineligible or weak hackathon submission | execution | Medium | High | Working Fire OS/Vega app, documented AWS integration if entering AWS mini challenge, source rights, actual device/simulator video per rules. [H1] |

## 8. Scale-up roadmap

| Period | Product / evidence milestone | Gate to continue |
|---|---|---|
| 0–3 months: pilot | One consenting streaming publisher or a controlled rights-cleared catalog; batch analyzer, human review, immutable sidecar, Fire TV player enforcement; compare against reference media and expert review | Playback timing and fail behavior recorded across target devices; no known high-severity misses in the bounded evaluation set; buyer agrees to measured pilot. A bounded set does not establish real-world safety. |
| 3–12 months: growth | SDK/sidecar contract, MediaConvert and alternative packager adapters, signed editorial workflow, ad/rendition mapping, fleet telemetry, legal and accessibility review | Two independent publisher integrations; measured time to publish, false-positive burden, and cost per media hour. |
| 12+ months: platform | Multi-tenant release governance, partner certification workflow, other TV clients with explicit capability matrices, optional verified alternative renditions | Repeatable paid renewals and independent evaluation. Expansion beyond Fire TV is a business option, not a Fire TV hackathon requirement. |

## 9. Judge and VC narrative hooks

1. **“A warning at the start of a movie cannot tell the player what to do at minute 42. Our publisher-side release file can.”**
2. **“When a new cut shifts the scene, Synaptic Shield refuses the old safety timeline instead of confidently pausing at the wrong frame.”**
3. **“We analyze a title once, review the intervention, and let the Fire TV player enforce that decision on every authorized play.”**

These are product-thesis lines until the timing/revision gates above are actually demonstrated.

## 10. Open questions and assumptions log

| Question / assumption | Current status | Fastest evidence |
|---|---|---|
| A publisher will pay for intervention rather than rely on warnings and existing QA | Unproven | Interviews with accessibility, playback, content ops, and procurement |
| First target OS/device and player can pause before the first hazardous frame | Unproven | Instrumented Fire TV/Vega playback spike on owned HLS |
| A reviewed sidecar stays frame-aligned across adaptive variants | Unproven | Multiple encode/seek tests and sidecar integrity checks |
| Ads can be reviewed or excluded by partner integration | Unproven | Map actual SSAI/client ad architecture with pilot publisher |
| EA IRIS may be embedded commercially | Unverified license/build suitability | Review repo license and legal terms; fallback independent detector/reference export |
| A human can approve a context note from nearby cues without spoilers | Unproven | Editorial protocol and reviewer study |
| User preference can safely set auto-skip for known hazard | Unproven | Accessibility research and clinical advice; default pre-event pause |
| Known unreviewed content should pause automatically | Product/clinical tradeoff | Pilot research; clearly label status and avoid a false “safe” state |
| Exact ITU-R BT.1702-3 thresholds for HDR and test environment | Not validated from the proposal | Read full standard and test lab guidance before any compliance claim |
| Fire TV track eligibility and deadline | Rules checked 28 Sep 2026; can change | Recheck official rules before submission. [H1] |

### Source register (primary wherever possible; accessed 28 Sep 2026)

**Amazon and event**

- [A1] Amazon Fire TV multimedia requirements: https://developer.amazon.com/docs/fire-tv/multimedia-app-requirements.html
- [A2] Amazon Vega WebGL best practices; private GPU buffers prevent extracting video frames as WebGL textures: https://developer.amazon.com/docs/vega/0.21/webview-webgl-best-practices
- [A3] Amazon Fire TV web app guide; older WebGL/Stick limitation: https://developer.amazon.com/docs/fire-tv/getting-started-with-web-apps.html
- [A4] Amazon cloud app streaming, secure on-device video pipe: https://developer.amazon.com/docs/cloud-app-program/amazon-cap-solution-details and Android secure-window behavior: https://developer.android.com/security/fraud-prevention/activities
- [A5] AWS MediaConvert timecodes and output timeline: https://docs.aws.amazon.com/mediaconvert/latest/ug/setting-up-timecode.html
- [A6] AWS Video on Demand solution architecture: https://docs.aws.amazon.com/solutions/latest/video-on-demand-on-aws/how-the-solution-works.html
- [A7] Amazon Vega media player: https://developer.amazon.com/docs/vega/0.23/media-player and https://www.developer.amazon.com/docs/vega/0.24/media-player-faq
- [A8] Amazon Nova invoke API: https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-invoke-api.html
- [H1] Official hackathon rules and Fire TV criteria: https://amazonappdev2026.devpost.com/rules

**Problem and standards**

- [P1] Epilepsy Foundation, photosensitivity and seizures: https://www.epilepsy.com/what-is-epilepsy/seizure-triggers/photosensitivity
- [P2] W3C WCAG 2.3.1 understanding: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold
- [P3] ITU-R BT.1702-3 landing page: https://www.itu.int/rec/R-REC-BT.1702-3-202311-I/en
- [P4] HBO Max accessibility help: https://help.hbomax.com/us-en/answer/detail/000002553
- [P5] CineGuard's own account of moderation and cut/region timestamp drift: https://cineguard.org/about

**Prior art and current builders**

- [C1] EA IRIS repository: https://github.com/electronicarts/IRIS
- [C2] HardingFPA product: https://www.hardingfpa.com/
- [C3] Caesar (HackTX): https://devpost.com/software/epilepsee
- [C4] Epilepsy Safe Viewer: https://devpost.com/software/epilepsy-safe-viewer
- [C5] Flikcer: https://devpost.com/software/flikcer-web-app-for-photosensitive-epilepsy-resolution
- [C6] EPI-LENS repository: https://github.com/Pi-0r-Tau/EPI-LENS
- [C7] Described, current event Fire TV project: https://github.com/moiz-lakkadkutta/described
- Additional direct overlap: https://github.com/user234683/seizure-flash-blocker and https://github.com/EzraMarks/strobe-blocker

**Research limit:** This is a targeted competitive pass, not an exhaustive patent search or clinical validation. Exact implementation, buyer interviews, benchmark results, and commercial permissions remain open.
