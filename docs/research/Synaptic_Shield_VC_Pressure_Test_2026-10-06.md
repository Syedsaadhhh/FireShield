# Synaptic Shield: VC pressure test and corrected research

**Review date:** 6 October 2026  
**Input:** `Synaptic_Shield_Hardened_Idea_File_2026-09-28.md`, supplied by the founder.  
**Project:** Syedsaadhhh/FireShield · Amazon Build, Ship, Shape · intended Fire TV track.  
**Decision:** **TEST the narrow publisher integration thesis. Do not present the business as validated or the prototype as medically protective.**

This review preserves the original document as a historical source. It supersedes its optimistic scores, novelty framing, buyer assertions, and incomplete release contract. It is desk research and engineering analysis: no interviews, customer commitments, detector benchmarks, app builds, device tests, clinical evaluations, or paid pilots were performed. Proposed thresholds below are experiment decisions, not achieved results. Imported MaxKmet templates informed competitor, pricing, distribution, weakness, and decision-memo structure; their B2C acquisition assumptions and numeric scoring were not transferred to this B2B product.

## 1. Investment committee view

**There is a credible accessibility problem and a plausible Fire TV demonstration. There is not yet evidence of a venture-scale company.** The strongest version of the idea is a small integration layer connecting an existing content review process to a publisher-controlled player. Its possible value is preventing an app from acting on review data for the wrong release, and making approved scene handling observable and testable.

The original proposal already made the essential correction: operate on authorized content inside a publisher's own app. Keep that boundary. A universal shield over Netflix, Prime Video, or arbitrary third-party playback has no demonstrated access path here. Amazon's Vega documentation specifically describes private video buffers that cannot be extracted into WebGL textures. That restriction does not mean Vega lacks WebGL: its documentation supports WebGL 2.0. [S12]

The biggest new finding is that **flashing-risk metadata and playback mitigation already exist in a major ecosystem**. Apple specifies HLS risk regions using `EXT-X-DATERANGE`; it also describes dimming and supported timeline indication, and publishes a reference algorithm. [S05–S07] The resulting inference: hazard timestamps connected to playback are an established category. This does not establish Apple's implementation of the proposed editorial, revision-rejection, or Fire TV workflow.

Commercial incumbents are stronger than the original comparison suggests. Interra BATON advertises PSE checks, correction, APIs, review tooling, and ABR-package QA. Telestream advertises optional PSE correction. These are vendor descriptions, not independently benchmarked performance. Nevertheless, they make “why not repair the video during ordinary QC?” the decisive buyer objection. [S08–S10]

**Recommended positioning:** “A release-aware playback integration that helps publishers apply reviewed scene-handling decisions in their own Fire TV app.” Avoid “first,” “seizure prevention,” “clinically safe,” “compliance guaranteed,” and “Amazon acquisition” as the commercial thesis.

| Investment question | Current assessment | What would change it |
| --- | --- | --- |
| Is the viewer problem real? | Supported at the category level; this intervention's benefit is untested | Observed usability benefit without false reassurance |
| Is there a paying publisher problem? | Unknown; severity for viewers does not prove procurement urgency | Recent incident/rework examples, named budget owner, integration commitment |
| Is it technically feasible? | Player controls are documented; pre-render behavior is unproven | Bounded device evidence showing intervention and revision rejection |
| Is the product meaningfully different? | Candidate workflow difference; strong prior art | Task comparison against corrected media, HLS markers, and existing QC exports |
| Is it defensible? | Detector, signatures, timestamps, and cloud architecture are reproducible | Reusable integrations, evaluation quality, trusted operations, renewal evidence |
| Is it venture-scale? | Unestablished; could be an SDK feature or specialist service | Repeatable distribution, adequate contracts, low integration burden, retention |
| Is it a promising hackathon thesis? | Yes, conditionally: a specific TV interaction plus a visible failure demonstration | A working target-platform demo and honest evidence |

## 2. What the original document needs to change

| Original statement or implication | Correction | Effect on the decision |
| --- | --- | --- |
| Version-bound hazard timelines are the central invention | A useful engineering mechanism; not demonstrated novelty. Apple already specifies streaming risk markers. No patent or freedom-to-operate conclusion was established. [S06] | Compete on workflow and implementation evidence |
| The backend gate has a “real operational buyer” | A plausible role is identified; no buyer has confirmed pain, budget, or authority | Replace “buyer validated” with “buyer hypothesis” |
| B + C is the winner, with 7–8/10 scores | It is the leading hypothesis for testing. Scores reflect optimism about an unbuilt future system | Use evidence gates and stop conditions |
| Current tools mainly detect or warn | BATON and Vidchecker also describe correction and broader workflow capabilities. [S08–S10] | Test against correcting content at source |
| Source scanning plus translated timecodes is sufficient | Scan the exact published visual outputs; mapping alone cannot establish unchanged hazard properties | Final-output review is part of release approval |
| A signed manifest means trustworthy intervention | It authenticates a statement under a trust policy. It does not establish detector accuracy, correct media binding, or timely rendering | Separate security, screening, and playback claims |
| `pause()` or `seek()` means protection | These commands do not prove which frames reached the display. A paused frame can also retain problematic imagery | Measure rendered output and surface transitions |
| Re-encoding/ABR inevitably causes timestamp drift | Correctly aligned variants need not drift. Edits, discontinuities, mapping errors, and transform effects must be handled separately | Diagnose actual failure mechanisms |
| Revocation can be immediate for all viewers | Offline clients and caches limit freshness. Define a lease, refresh behavior, and stale-policy outcome | Bound the guarantee explicitly |
| EA IRIS commercial reuse is unverified | Its checked `LICENSE.txt` is BSD-3-Clause style: redistribution permitted with conditions and disclaimer. Dependencies and actual integration still need review. [S04] | Candidate screening baseline; not certification |
| HDR formulas lack primary support | Current W3C guidance includes HDR-specific definitions and highest-dynamic-range testing. That does not validate this detector or justify HDR scope now. [S02] | Keep first prototype SDR |
| Described is a working Vega competitor | Its README describes Fire OS as the shipping target and Vega as an experimental template not built/run. We did not independently test either. [S17] | Compare actual claimed target, avoid inflated overlap |
| The current event gallery can establish uniqueness | On this access date, the official gallery states it has not been published. [S20] | Current-entry coverage is incomplete |

These corrections narrow the promise while preserving the useful idea. They do not establish that the remaining workflow is unique, effective, or commercially desired.

## 3. Problem evidence and claims discipline

The Epilepsy Foundation describes visual triggers affecting about **3% of people with epilepsy**. This is not 3% of the population or streaming viewers. It establishes a serious category of need, not the number of customers who will use this feature or the number of publishers who will buy it. Do not aggregate autism, migraine, epilepsy, sensory discomfort, or household members into a revenue TAM without separate definitions and demand evidence. [S01]

W3C's criterion considers flash count and general/red thresholds, with area and viewing assumptions. Its guidance addresses full-screen evaluation and non-sRGB/HDR content. A simple whole-frame luminance average or one three-flashes-per-second rule is not a complete assessment. Nor does offering a skip button establish content conformance. Use the relevant standard and test conditions for each eventual claim. [S02]

ITU lists BT.1702-3 (November 2023) as in force. This review confirmed that landing-page status, but did not validate an implementation against the full recommendation. WCAG, broadcast guidance, detector outputs, editorial approval, clinical risk, and jurisdictional obligations are different questions. This report makes no legal classification or clinical efficacy determination. [S03]

**Three claims require three evidence streams:**

1. **Screening:** the analyzer flags particular criteria under specified test conditions. Evidence: benchmark labels, configuration, version, misses and false positives.
2. **Playback:** this app avoided displaying specified intervals in measured test runs. Evidence: presented-frame observations, device configuration, timing distribution and failures.
3. **Viewer value:** people understood the mode and completed a task more comfortably or predictably. Evidence: usability research. It cannot be substituted for a seizure-prevention trial.

No one of these establishes the other two. The terms “reviewed” and “no flagged interval found” must retain their scope. An unreviewed title, missing metadata, or a negative detector result must never silently become a green “safe” badge.

## 4. Competitive reality and substitutes

| Alternative | Verified evidence or source scope | Why a buyer might prefer it | Remaining hypothesis to test |
| --- | --- | --- | --- |
| Apple accessibility + HLS risk markers | Dimming, supported timeline indication, documented risk-region metadata, public reference algorithm. [S05–S07] | Built into the ecosystem; publisher metadata has an existing format | Fire TV delivery plus audited release rejection may matter; not proven |
| BATON | Vendor-described PSE analysis/correction, ABR QC, APIs and review workflow. [S08] | Adds to existing media QA operations | Consume its outputs rather than replace the QA stack |
| HardingFPA | Vendor describes guideline analysis and frame-level failure reporting. [S09] | Established specialist evaluation workflow | Bridge approved results to app behavior if customers need it |
| Vidchecker | Vendor describes an optional luminance-flash correction capability. [S10] | Repair the delivered file instead of relying on runtime bypass | Does publisher need viewer-selectable handling despite correction? |
| EA IRIS | Open-source C++ analysis of luminance/red/patterns, CSV/JSON output and disclaimer. License inspected. [S04] | Low licensing barrier and reusable screening baseline | A deployable, reviewed playback integration could reduce integration work |
| FFmpeg | Public `photosensitivity` filter with sampling and blending controls. [S11] | Existing pipeline can transform content | Transformation needs evaluation; it is not automatically a standards test |
| TikTok | Its 2020 announcement described creator warnings and a planned viewer Skip All feature. [S15] | Shows avoidance is an established product approach | Long-form continuity and release binding may differ; current rollout not verified |
| CineGuard | Moderated warnings and timestamps; admits cut/intro drift. [S16] | Viewer can decide using a public database | Exact-release application could improve timing in a controlled app |
| Described | Self-described Fire OS audio-description workflow using Bedrock/Polly/HLS; Vega template deferred. [S17] | Better continuity/accessibility through alternate audio | Scene context is adjacent functionality, not a defensible core |
| An internal spreadsheet + player rule | Buyer-workflow hypothesis; not an observed customer practice | Cheap when catalog and incident volume are small | Quantify mistakes and maintenance before claiming replacement value |
| Correct or remove the risky scene | Operational substitute, informed by correction tools above | Removes dependence on metadata/player reliability | Does it create unacceptable editorial, rights, or turnaround costs? |
| Keep warnings and accept current practice | Unmeasured status-quo substitute | No integration or procurement effort | Is there a compelling trigger to change now? |

Earlier browser/hackathon examples in the supplied document remain useful discovery leads. Their detailed implementation and current performance were not revalidated in this pass; do not cite the original comparison as an exhaustive competitive audit. The official event gallery was unavailable for project inspection. No universal negative such as “nobody does this” is justified. [S20]

**The skeptical comparison must be task-based:** take the same authorized release, give an operator existing QA output, and compare time to approve, errors after a new cut, player integration work, and viewer interruption. Showing that our own JSON differs from a competitor's feature list is insufficient.

## 5. Corrected product thesis

**Proposed first user:** a viewer who deliberately chooses reviewed scene handling in a participating publisher app. The mode offers predictable handling of approved intervals and makes coverage visible. A TV is appropriate because the intervention belongs inside the long-form viewing experience and must be operable with a remote; it need not depend on a second screen.

**Proposed first customer:** a publisher with an owned or modifiable Fire TV player, authorized SDR VOD, an existing content-review owner, and a recurring reason to manage scene interventions. These are eligibility criteria for a pilot, not evidence that the segment buys the product. Exclude prospects whose app supplier will not permit integration.

**Offer to test:** an adapter accepts reviewed QA findings, binds them to a released media package, and supplies a small Fire TV playback module plus an audit record. Start detector-agnostic. IRIS can be a development baseline; recognized commercial QA exports may be a better customer input. Building a new QA dashboard is optional until a user shows a gap in existing tools.

The player should distinguish coverage, upcoming intervention, successful completion, and inability to continue in the chosen mode. For the demonstration, use “Reviewed scene handling available for this release” rather than “This movie is safe.” Allow exit from the mode; do not silently downgrade it after a verification error. Preference can be local and need not encode a diagnosis.

**Defer:** universal interception, HDR, live streams, ads, regional-release automation, auto-generated alternate video, clinical personalization, broad trigger classes, and multi-platform SDK coverage. A manually approved context note is enough to test narrative continuity. Offline model drafting can come later if it improves editor throughput; it does not strengthen the timing claim.

## 6. Engineering pressure test: where the design can fail

### Published media identity

A mezzanine digest and a transcode release ID are different identities. The player cannot prove that a fetched stream matches a mezzanine simply by comparing those strings. Define an immutable release descriptor that names the exact package, manifests and visual renditions, references their origin object versions or digests, and records review coverage. Avoid assuming an S3 ETag is a content SHA-256; AWS documents ETag behavior varying with upload/encryption conditions. [S27] Hashing a changing playlist also does not prove the whole stream.

For a controlled VOD prototype, publish media to immutable paths and have the backend issue a signed release descriptor bound to those paths and identifiers. This establishes a bounded trust chain, not client-side verification of every decoded frame. If stronger hostile-media substitution protection is needed, design segment authentication separately. Explain what the trusted backend, CDN, client, and reviewer each attest to.

### Screening after transformation

Run preliminary screening if useful, then evaluate the exact visual outputs that will be published. Scaling, overlays, color conversion, frame-rate changes, and edited boundaries can affect the signal being tested. Reusing a source timeline is a hypothesis requiring output validation. For an ABR set, approve each published visual variant or document and validate a justified coverage method; do not infer coverage from a shared title ID.

Merge coincident/overlapping findings into reviewed intervention windows without erasing the contributing criteria. Separate machine flag boundaries from the wider editorial bypass interval and from the operational guard. Store detector parameters, analyzer build, criteria version, reviewer decision, and input/output identities.

### Timebases and discontinuities

MediaConvert distinguishes input, output, and emitted timecode settings and uses frame-number timecodes. A `presentation_ms` sidecar therefore needs an explicit conversion and mapping; it cannot be copied directly from an arbitrary timecode display. [S13] Use integer presentation timestamps with a named timescale, duration, endpoint convention, and clock origin. Source frame number, decode timestamp, presentation timestamp and wall clock are not interchangeable.

First version: a single VOD presentation with no ads or timeline discontinuities. Later: name each discontinuity/period and map hazards within it; HLS defines discontinuity signaling and sequence handling. [S29] Apple's markers are a possible export, not evidence of automatic Fire TV enforcement. [S06] Mapping their positions to the app remains an integration responsibility.

### Rendering and transport controls

Vega documents play, pause and position-based seek. Those controls support a feasibility spike but do not specify a medical protection guarantee. [S14] Media3 exposes presented-frame metadata and configurable seek behavior; sync-point seeking trades accuracy for speed, and decoding may start earlier than the requested point before discarded output. [S18, S26] Frame callbacks can support instrumentation but are not themselves a guaranteed cancellation mechanism.

**A successful API call is not the measurement.** Record the last displayed harmless frame, intervention surface appearance, and first frame after bypass. During startup and seek, prevent presentation until target position and release policy are checked. A pause can freeze a problematic image; a seek can create a visual transition. Test native video-surface composition and any neutral replacement surface. Do not assume that a React/HTML overlay suppresses a separately composited video surface on every device.

Preload policy locally. Choose the pre-event guard from measured scheduler, decoder/render, state-transition and mapping uncertainty, with a declared margin. Report tails and worst observed cases. A large guard reduces timing risk but interrupts more content; measure that tradeoff. No numerical guard is justified by this desk review, and a finite test cannot prove a universal bound.

### Contract integrity and failure behavior

Replace the original informal manifest with a documented signing envelope. Proposed contents:

| Area | Required design decisions |
| --- | --- |
| Identity | Tenant, release/cut, region if relevant, exact package/rendition identity, coverage list |
| Analysis | Analyzer/version/configuration, criteria, output artifact references, review decision |
| Timing | Timescale and origin; per-interval start-inclusive/end-exclusive bounds; reviewed resume target; future discontinuity IDs |
| Policy | Supported actions, mode version, bounded device capability profile, missing-policy behavior |
| Freshness | Issued time, expiry/lease, revision or sequence, signed revocation/freshness mechanism |
| Signature | Pinned trust policy, allowed algorithm, key ID, exact signed bytes or specified canonicalization; signature outside signed payload |

Do not put a digest inside the payload and then define it as hashing the entire payload including itself. Do not accept attacker-chosen keys or algorithms. Validation includes duplicate fields, interval bounds, supported schema, coverage, matching release, expiry and replay under the chosen policy. RFC 8785 is one existing canonical-JSON option to evaluate; use a tested library rather than inventing serialization. [S28] Signatures cannot fix a wrong reviewer decision or a wrong clock mapping.

There is no atomic transaction across all storage/CDN/client caches. Upload immutable media and review artifacts first, then publish a versioned release pointer when both are ready. Existing sessions pin a release under an explicit freshness policy. On revocation, online clients refresh within the designed bound; offline protected sessions expire at the lease boundary. “Immediate everywhere” is not a supported claim.

For the bounded prototype, an opted-in reviewed session that lacks a valid matching descriptor must stop before starting/resuming video, show the reason, and offer exit. Seek timeout, resume inside a flagged window, unknown variant, expired lease, failed surface transition or stale release all return to that blocked state. This is a proposed product policy, to be tested for comprehension; it does not label an unseen asset hazardous or certify an alternative mode.

### Minimal Amazon implementation to recommend, not build in this review

Start with one Fire OS target and a Kotlin/Media3 feasibility spike **if the available device and team environment support it**. Android instrumentation is useful, but Fire TV device compatibility remains untested here. Vega is a valid alternative when its simulator/device and team familiarity are better; test its documented media APIs first. Do not commit to two OS implementations before the critical interaction works.

Proposed backend: S3 for immutable artifacts; a batch/container job for analysis; one review/export step; MediaConvert for controlled VOD; CloudFront for distribution; signing with a managed key; a small metadata service only if needed. Step Functions becomes useful when orchestration warrants it. AWS's published VOD architecture supplies relevant media-lifecycle patterns, but does not implement this product. [S21] Nova/Bedrock drafting is optional and outside enforcement. Avoid adding AgentCore, Alexa, or numerous services merely to name them in a submission.

## 7. Business pressure test

### Who pays and who can say no

The original accessibility/product buyer is a hypothesis. Content operations may own review, playback engineering owns integration, legal/content licensing may veto transformations, and finance must fund the work. Find the actual economic buyer in discovery. An accessibility champion without engineering time or a budget is not a design partner.

Small publishers can be reachable but may have low volume and little removable work. Large publishers can have more spend but mature QA, custom players, procurement requirements and little interest in a new dependency. No segment has been validated. The sensible early filter is operational control plus a recurring event, not company size alone.

**Distribution complication:** a branded Fire TV app does not imply publisher control of its code. Uscreen and Vimeo describe branded TV-app offerings including Fire TV. A publisher on such a platform may need its supplier to adopt the integration. This is an inference from platform-provided apps, not evidence of an actual integration restriction for any named customer. [S22–S23] Interview app providers as potential channel partners as well as publishers as buyers. Providers also have the capability to build the feature themselves.

### Pricing and economics

Test a paid fixed-scope integration pilot before setting recurring pricing. Possible later model: platform/support fee plus clearly defined analyzed-output hours. A commercial analyzer licence, human review, transcode retries, storage/egress, customer-specific adapters, incident support and device QA all affect cost. Charging by uploaded source hour while processing six outputs hides work unless the margin model accounts for it. Per-viewer diagnosis profiling is unnecessary.

**Buyer-value model:**

`annual net value = releases × removable hours/release × loaded cost/hour + documented avoided rework − new incremental review cost − buyer integration/maintenance − product fee − other incremental operating cost`

Only count removable work; exclude review that remains necessary. Avoid double counting existing review or charging infrastructure twice if it is included in the fee. Do not assign speculative monetary value to prevented medical incidents or assumed legal exposure.

**Illustrative stress test, entirely invented inputs:** 300 releases/year × 2 removable hours × $60/hour = $36,000 gross savings. Add 0.5 new review hours/release at $60 = $9,000; $15,000 first-year integration and $12,000 annual product fee consume the entire $36,000. Year two still depends on maintenance, support and actual saved hours. This is not observed pricing, salary data or ROI; it shows how modest integration effort can erase an attractive labor story.

**Seller model:** `account contribution = fee − account-specific compute/licences/review/support − annualized adapter and device-maintenance cost`. Serving a cached sidecar is inexpensive relative to repeated inference in principle, but custom integration and assurance can dominate. We have no measured unit costs, gross margin, sales cycle, CAC, LTV, churn or renewal data. Do not import generic B2C ratios from the research toolkit.

### Market sizing and moat

There is no defensible TAM number yet. Build a prospect census: active Fire TV service; owned/modifiable player; qualifying content; rights; review owner; recurring pain; budget; reachable procurement. Remove brands sharing a procurement/platform buyer rather than treating every channel as a separate contract. Count qualified accounts before multiplying by an evidenced contract value.

For scale arithmetic only, at a hypothetical $12,000 annual contract, $10 million recurring revenue requires about 834 paying accounts before churn; $100 million requires about 8,334. Neither contract size nor account availability is supported. Platform distribution could change this model, but concentrates bargaining power and adoption risk. A small sustainable specialist business may still be worthwhile even if the venture case fails.

Potential defensibility: a reusable integration library, independently evaluated mapping/render behavior, low operator burden, operational history and trusted partners. These are capabilities to earn. Publisher rights usually prevent freely sharing media/reviews across customers; a cross-publisher data network effect is not established. Signatures and use of AWS are infrastructure, not exclusive intellectual property. “Amazon buys us” is not a substitute for renewal economics.

## 8. Riskiest assumptions and experiments

**Business riskiest assumption:** a publisher with a modifiable player has enough recurring unmet pain to fund integration instead of correcting the content, using existing metadata, or maintaining warnings. **Technical riskiest assumption:** the selected device/app can suppress the first flagged frame across supported transport actions. Test both before expanding the product.

The following are founder-selected decision thresholds for a bounded first cycle. They are not clinical thresholds, market benchmarks, or completed evidence.

| Experiment | Method and evidence | Predeclared decision |
| --- | --- | --- |
| Buyer workflow discovery | 8–12 interviews across qualifying publishers, playback leads, content reviewers and app providers. Ask for the last actual release problem, workaround, artifacts, ownership, cost and alternative choice | Advance commercial pilot only after at least two independent prospects show a concrete recurring workflow and one names an owner, content access and engineering window. Interviews alone do not pass willingness to pay |
| Alternative confrontation | Show the same scenario with existing correction, warning, HLS-marker and proposed integration approaches; ask who would choose which and why | If qualifying prospects consistently choose cheaper correction/status quo and cannot name unmet work, revise or stop the standalone product thesis |
| Timing feasibility spike | Owned SDR VOD with harmless markers; selected Fire TV device/simulator; frame observation and logs for startup, replay, forward/back seek, suspend/resume, buffer recovery and guard boundaries | Any displayed marked frame, unsupported composition behavior or unbounded state transition blocks the avoidance claim for that configuration. Passing a finite suite supports only its tested scope |
| Release mismatch injection | Change intro/cut, package identity, signature, expiry and supported variant. Attempt stale descriptor reuse and interrupted publication | All specified negative cases must stop the reviewed session before video resumes; retain a result for every case |
| Analyzer evaluation | Version-pinned IRIS/config; held-out positives/negatives and boundary cases; expert/reference comparison where accessible | Publish misses, false alarms and timing errors by criterion. Known unresolved misses block efficacy claims; no universal accuracy number without a defined corpus |
| Operator and viewer task comparison | Operator creates/revises interventions against existing method; viewers use harmless stand-ins to navigate and explain coverage/status | Proceed only if measured task benefit outweighs review/interruptions and participants understand coverage limits. Record disagreement and sample limitations |
| Paid design-partner pilot | Agreed content, target device, integration hours, acceptance outcomes, fee, review owner, support responsibility, exit terms | A signed paid scope and integration resource pass commitment; a free email compliment or vague LOI does not |

Interview prompts: “Walk me through the last title where this mattered”; “Which file and player release did your review apply to?”; “What happens after a new cut?”; “Who maintains the app?”; “Why not correct the scene?”; “Which budget pays?”; “What would prevent a pilot?” Obtain permission before retaining private artifacts. Do not invent verified-pain records from public prevalence.

TRACE's public `pse-test-media` project supplies configurable spatial, time/color, and generated-video cases associated with Jordan's 2025 conformance-evaluation paper. It offers a better benchmark starting point than one dramatic clip; the presence of a generator does not validate a detector. Its README warns generated media may be hazardous. This review did not run it or read the complete paper behind the access barrier, and makes no claimed ranking of analyzers. [S24–S25] Use automated/offline benchmark analysis under an appropriate evaluation protocol. Public demos and usability tasks should use harmless scene markers, not provocative flashes or experiments on seizure-prone participants.

## 9. Evidence gates: current state

These use FireShield's existing original G1–G6 definitions. “FAIL” below refers to current delivery evidence, not an irreversible verdict on the idea.

| Gate | State | Evidence and outstanding condition |
| --- | --- | --- |
| G1 Problem | CONDITIONAL | Viewer hazard category supported; recurring publisher workflow and specific viewer task unobserved |
| G2 Gap | CONDITIONAL | Candidate integration benefit; Apple markers and correction tools sharply limit novelty. Comparative task evidence missing |
| G3 Device feasibility | CONDITIONAL | Relevant media controls documented; target platform, rendering, timing and fail behavior not demonstrated |
| G4 User value | UNASSESSED | No operator or viewer task study; context and interruption benefit untested |
| G5 Business | UNASSESSED | No confirmed budget, design partner, price, distribution permission, cost or paid commitment |
| G6 Delivery | FAIL at review time | FireShield is documentation only; no runnable Fire TV application or platform demonstration |

The universal third-party interception variant is rejected for this project because there is no demonstrated authorized implementation path. The publisher release gate alone is easier to demonstrate but risks duplicating QA reports. The release gate plus controlled player is the leading **test candidate**, not a validated winner. AI context is optional and carries weak differentiation.

## 10. Hackathon scope and demonstration recommendation

Official rules checked 6 October list the deadline as **23 October 2026, 12:00 pm PDT (19:00 UTC)**. Fire TV entries need a demo-ready app on Fire OS or Vega and video on an actual device or accepted simulator. A functional repository needs setup/run instructions and either an open-source public licence or the specified private access. Judging covers implementation, design, impact and idea quality; actionable friction logs can earn a bonus. The source import alone is not a meaningful open-source feature. Recheck the rules before submission. [S19]

**Recommended bounded prototype:** one owned SDR VOD title, one output to start, one selected OS/device, manually reviewed intervals, immutable release identity, verified descriptor, pre-event handling, controlled skip/resume, and explicit mismatch blocking. Add a second visual variant only after the one-output case works. Keep ad-free VOD for the submitted promise. Do not demonstrate broader capabilities through diagrams alone.

Demo sequence:

1. Show an operator-reviewed interval for release A using harmless footage and a clear test marker.
2. Run the TV app in the selected mode: intervene before the marker, offer a remote-friendly bypass, and resume at the approved target.
3. Switch to release B with an edited intro while retaining A's metadata: show rejection before playback rather than intervention at the wrong scene.
4. Publish B's reviewed descriptor and repeat the flow. Show instrumentation and the declared test scope.

**The memorable moment is the stale-release rejection.** It turns an invisible engineering issue into a visible product behavior. It still needs competitor comparison and user research; a persuasive demo is not proof of demand.

Proposed schedule if implementation is authorized later: October 6–8, platform/control spike and interview recruitment; 9–13, bounded release/player flow plus customer conversations; 14–18, negative-case and output-observation evaluation; 19–21, reproducibility and video; 22, buffer/final rule check. Freeze expansion when the core gate fails. No app implementation or outreach was initiated during this research review.

## 11. Corrected founder narrative

**Problem:** publishers can have reviewed scene information without a reliable way for their own TV app to apply it to the correct media release. Whether this creates significant recurring work is what we are testing.

**Product:** Synaptic Shield connects reviewed intervals to a released video package and a controlled Fire TV player. The app checks coverage and release identity, handles approved intervals, and refuses stale metadata in the selected mode.

**Difference:** existing detectors, corrected files and streaming risk markers are important alternatives. Our proposed contribution is a tested Fire TV integration and an auditable release/review workflow, not invention of detection or hazard timestamps.

**Evidence today:** verified category need, relevant player APIs and strong competitive precedents. No customer revenue, measured medical benefit, completed device evaluation or established moat.

**Next proof:** a bounded timing/revision experiment and a customer who commits engineering time and pays for a specific unmet workflow.

**Stop/pivot:** if presentation cannot be controlled reliably, retain publisher review/export tooling only where customers actually need it. If publishers prefer existing QA correction and do not fund integration, consider a partner feature/contribution rather than a standalone venture. Do not relabel the same failed demand hypothesis as a broader sensory-safety platform.

## 12. Primary source register and access limitations

All sources checked during this review on 6 October 2026. Product capabilities are vendor claims unless otherwise stated. Repository READMEs and licences were inspected, not executed. Source IDs are local to this report; they do not imply independent certification.

| ID | Source | Supports / limitation |
| --- | --- | --- |
| S01 | [Epilepsy Foundation: Photosensitivity and seizures](https://www.epilepsy.com/what-is-epilepsy/seizure-triggers/photosensitivity) | Approximate 3% denominator is people with epilepsy; no buyer TAM |
| S02 | [W3C: Understanding SC 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold) | Flash criteria, viewing assumptions, full-screen/HDR guidance; no product validation |
| S03 | [ITU BT.1702-3](https://www.itu.int/rec/R-REC-BT.1702-3-202311-I/en) | Landing page says in force; full implementation conformance not checked |
| S04 | [EA IRIS](https://github.com/electronicarts/IRIS), [licence](https://github.com/electronicarts/IRIS/blob/main/LICENSE.txt) | README screening/export scope and inspected licence; not built or benchmarked |
| S05 | [Apple accessibility features](https://www.apple.com/accessibility/features/) | Dimming and supported timeline indication, including Apple TV 4K; no all-content guarantee |
| S06 | [Apple HLS authoring appendixes](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices-appendixes) | Official indexed text describes risk-region DATERANGE class and level; direct page is JS-only in this reader |
| S07 | [Apple VideoFlashingReduction](https://github.com/apple-aiml-research/VideoFlashingReduction) | Reference implementations and MIT-labelled repository; not benchmarked here |
| S08 | [Interra BATON](https://www.interrasystems.com/file-based-qc.php) | Vendor-described PSE/correction, ABR, workflow/API support |
| S09 | [HardingFPA](https://www.hardingfpa.com/) | Vendor-described guideline analysis and detailed reports |
| S10 | [Telestream Vidchecker options](https://www.telestream.net/vidchecker/options.htm) | Vendor-described optional luminance-flash correction; licence/configuration matter |
| S11 | [FFmpeg filters: photosensitivity](https://www.ffmpeg.org/ffmpeg-filters.html#photosensitivity) | Existing filtering controls; not automatically conformance assessment |
| S12 | [Vega WebGL guidance](https://developer.amazon.com/docs/vega/0.21/webview-webgl-best-practices) | WebGL 2.0 support and private video buffers; scope is this documented platform |
| S13 | [AWS MediaConvert timecodes](https://docs.aws.amazon.com/mediaconvert/latest/ug/setting-up-timecode.html) | Distinct timelines/settings and frame-number timecodes |
| S14 | [Vega media-player FAQ](https://www.developer.amazon.com/docs/vega/0.24/media-player-faq) | Documented play/pause/seek; no measured intervention guarantee |
| S15 | [TikTok accessibility announcement, 24 Nov 2020](https://newsroom.tiktok.com/making-tiktok-more-accessible-to-people-with-photosensitive-epilepsy?lang=en) | Historical warning/avoidance precedent; current rollout not established |
| S16 | [CineGuard: About](https://cineguard.org/about) | Moderated timecodes and disclosed cut/intro drift |
| S17 | [Described README](https://github.com/moiz-lakkadkutta/described) | Author's Fire OS/audio-description and deferred Vega claims; not tested |
| S18 | [Android Media3 VideoFrameMetadataListener](https://developer.android.com/reference/androidx/media3/exoplayer/video/VideoFrameMetadataListener) | Instrumentation with frame presentation/release metadata; not a suppression promise |
| S19 | [Official hackathon rules](https://amazonappdev2026.devpost.com/rules) | Deadline, platform/repository/demo requirements and judging; recheck for changes |
| S20 | [Official event gallery](https://amazonappdev2026.devpost.com/project-gallery) | Unpublished at access; no exhaustive current-event competitor inspection |
| S21 | [AWS Video on Demand architecture](https://docs.aws.amazon.com/solutions/latest/video-on-demand-on-aws/how-the-solution-works.html) | Media-lifecycle infrastructure example; not this application |
| S22 | [Uscreen TV-app overview](https://help.uscreen.tv/en/articles/6708491-uscreen-mobile-tv-apps-overview) | Platform-built branded apps including Fire TV; no integration permission verified |
| S23 | [Vimeo OTT branded-app offering](https://help.vimeo.com/hc/en-us/articles/12427344749073-Why-branded-apps-are-a-good-investment-for-Vimeo-OTT) | Branded apps including Fire TV; no partner commitment or prospect census |
| S24 | [TRACE pse-test-media](https://github.com/traceRERC/pse-test-media) | Configurable benchmark generator; hazard warning; no local execution |
| S25 | [Jordan 2025 paper DOI](https://doi.org/10.1007/978-3-031-93848-1_7), [PubMed](https://pubmed.ncbi.nlm.nih.gov/40656147/), [author version](https://pmc.ncbi.nlm.nih.gov/articles/PMC12249941/) | Bibliographic record, indexed abstract and author repo inspected; full author-version page blocked by verification |
| S26 | [Android Media3 SeekParameters](https://developer.android.com/reference/androidx/media3/exoplayer/SeekParameters) | Exact/sync-point seeking and internal decoding behavior |
| S27 | [AWS S3 upload integrity](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html) | Checksums and ETag limitations; choose explicit integrity fields |
| S28 | [RFC 8785: JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785) | Existing canonicalization specification, not a full trust policy |
| S29 | [RFC 8216: HTTP Live Streaming](https://www.rfc-editor.org/rfc/rfc8216) | Playlist/date-range/discontinuity semantics; client enforcement still separate |

Research limits: targeted public-source review; no exhaustive patent search, confidential vendor audit, unpublished event submissions, production integration, rights clearance, local detector execution, medical efficacy or customer validation. Absence of a feature in a public page was not treated as proof that a competitor cannot offer it.
