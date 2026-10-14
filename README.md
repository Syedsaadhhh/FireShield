# FireShield
## FireTrace + Synaptic Shield

FireShield is a B2B prototype for streaming publishers and media platforms that control their own Fire TV / Android TV player and catalog. It brings **FireTrace's playback continuity and diagnostic loop** together with **Synaptic Shield's signed, version-bound presentation policy and release gate**.

We are building a publisher SDK and release workflow that checks whether a viewer's chosen presentation was actually restored after an interruption, and gives developers structured evidence when recovery diverges.

## The problem

Returning to the same timestamp is only one part of playback recovery. Captions, audio tracks, presentation settings, and the exact content edition also need to match. A changed intro, re-encoded rendition, or dropped track can make an apparently successful resume incorrect.

FireShield connects the publisher's approved media version to the viewer's saved presentation state, then compares that expectation with the player's observed state.

## How the two projects fit together

| Layer | Role in the merged prototype |
| --- | --- |
| **FireTrace** | Versioned checkpoints, post-interruption recovery verification, redacted incident receipts, and bounded developer retests |
| **Synaptic Shield** | Ed25519-signed publisher contracts, rendition and playlist binding, visual-hazard screening candidates, human release review, and playback intervention |
| **FireShield** | The integrated publisher workflow joining approved content policy, player continuity, mismatch handling, and regression evidence |

## The intended end-to-end flow

1. A publisher screens authorized content, reviews findings, and approves a version-bound release contract.
2. The viewer chooses supported captions, audio, and presentation settings in the participating TV app.
3. The app saves a versioned checkpoint tied to the active content revision and rendition.
4. After an interruption, the player restores the session and verifies the observed position, tracks, and presentation against that checkpoint and release policy.
5. A verified recovery resumes playback. An unresolved mismatch holds playback and produces a redacted incident receipt.
6. The developer uses the receipt to run an allowlisted regression scenario and check the failure or fix.

That is the target integrated behavior. Native recovery gating and the receipt-to-retest path are still being completed.

## Who it is for

- Streaming publishers integrating presentation policy and reliable recovery into their own TV apps.
- Player engineering and QA teams diagnosing caption loss, track mismatches, stale editions, and interruption failures.
- Media platforms needing tenant-scoped release records, key rotation, human review, and an auditable publishing workflow.

The B2B direction is a reusable SDK and publisher integration workflow. Commercial demand, pricing, and customer traction have not yet been validated.

## Current development status

| Area | Evidence and remaining work |
| --- | --- |
| Python reference implementation | Contracts, continuity, screening, local release pipeline, and bounded retest are implemented; **55 tests passed** on the inspected Windows workspace |
| Android/Kotlin harness | Media3 ExoPlayer, checkpointing, recovery inspection, and diagnostic controls exist; native integration corrections and fresh build verification are ongoing |
| Emulator / Fire TV runtime | End-to-end recovery has not yet been verified on a target runtime |
| AWS integration | Workflow definitions and readiness/dry-run planning exist; live cloud execution is pending |

Python test results are reference/model evidence, not measured Fire TV decoder performance. Deterministic ablation results are not real-world detection guarantees.

This repository currently holds project documentation and licensing. Application source is maintained in the local working project while the active native integration run continues. Research reports and agent prompts stay outside the current repository tree.

## Scope

FireShield operates inside a participating publisher's player using owned or authorized media. It does not inspect protected playback in unrelated applications.

Screening emits candidates for human review. The prototype is not medical software or a certified broadcast compliance tool, and no zero-frame leakage or clinical benefit is claimed. Interactive testing uses benign media; flicker/strobe fixtures remain headless.

## License

[Apache License 2.0](LICENSE), matching the license already selected for the local FireShield implementation.
