# Applying the validation toolkit to FireShield

Status: toolkit imported; product research and customer validation have not yet been performed. The current assignment is to evaluate the user's forthcoming research document and strengthen the idea research. The later feasibility/prototype steps below are proposed validation actions to recommend, rather than an instruction to build the app during this review.

## What was adopted

The MIT-licensed MaxKmet research templates supply useful structures for competitor analysis, pricing hypotheses, distribution, acquisition economics, identifying weak assumptions, and writing a decision memo.

[Imported references](../../third_party/idea-validation-agents/README.md) are pinned to commit 3a4c800a3022e73e9997533b603c79b60ee7597a. This is an attributed import into FireShield, not a new GitHub fork or an installed assistant plugin.

Use the templates selectively after the user's researched concept arrives. Do not restart a broad idea-generation interview when the supplied research already answers the relevant questions.

## Known limitations to account for

1. The upstream workflow targets indie B2C mobile apps. Research Amazon Appstore, built-in device features, and the actual TV user task before transferring mobile-app assumptions.
2. The pricing template estimates price-sensitivity thresholds from competitor prices. Record them as pricing hypotheses; it does not contain observed survey responses from FireShield's customers.
3. Desire multipliers, retention values, acquisition benchmarks, viral coefficients, and market capture rates require sources or explicit scenario labels. Unknown values remain unknown.
4. The scoring document lists six weights but describes confidence and missing-input discounts in terms of seven dimensions. Do not report its score as a calibrated probability of success or apply the inconsistent denominator silently.
5. Several rubrics contain TODOs, including weakness severity and retention benchmarks.
6. The workflow reads user_extraction.json and weighted_signals.json without specifying their generation inside the validation chain. Map these to actual evidence before running downstream stages.
7. Prompt instructions cannot create access to unavailable data sources, interview users, or run autonomous background work.

## Sequence for this project

1. Import the existing research and identify the exact target user, situation, task, proposed prototypes, and strongest assumptions.
2. Gather evidence and counterevidence from official device documentation, verified competitor products, and relevant user accounts. Record sources and dates in the evidence register.
3. Compare direct competitors, substitutes, and built-in Amazon capabilities against that exact task. Include what they already do well.
4. Demonstrate the highest-risk required interaction on the intended device or accepted simulator.
5. Compare one complete prototype flow with the current workaround using predeclared task metrics.
6. Examine buyer, pricing, reach, recurring use, and operating costs. Separate observed behavior from desk-research estimates.
7. Write a decision memo with the strongest supporting evidence, the strongest counterevidence, the next experiment, and stop/revise conditions.
8. Prepare the platform demonstration and submission after the core claims have been tested.

Useful upstream templates:
- [Competitors](../../third_party/idea-validation-agents/skills/competitor-mapper/SKILL.md)
- [Pricing](../../third_party/idea-validation-agents/skills/pricing-and-wtp/SKILL.md)
- [Distribution](../../third_party/idea-validation-agents/skills/distribution-analysis/SKILL.md)
- [Acquisition economics](../../third_party/idea-validation-agents/skills/cac-modeler/SKILL.md)
- [Weakness detection](../../third_party/idea-validation-agents/skills/weakness-detection/SKILL.md)
- [Decision memo](../../third_party/idea-validation-agents/skills/decision-memo/SKILL.md)

## FireShield evidence gates

The following gates are original FireShield planning guidance. They are not recovered kfros files, an exact replacement for its implementation, or an executable gate engine.

| Gate | Question | Evidence needed |
| --- | --- | --- |
| G1: Problem | Is there a specific recurring user problem? | Recent examples, consequences, current workaround, and target audience |
| G2: Gap | Does the solution improve on existing alternatives? | Task-level competitor/substitute comparison and counterexamples |
| G3: Device feasibility | Can the required interaction work on the intended TV platform? | Official capability documentation and an actual feasibility demonstration |
| G4: User value | Does the prototype improve the task? | Observed task results against the baseline, with test limitations |
| G5: Business | Is a plausible adoption and payment path supported? | Buyer evidence, commitment signals, distribution, and cost scenarios |
| G6: Delivery | Can users and judges reproduce the working experience? | Runnable repository, required platform demo, instructions, and submission materials |

Gate states:
- **UNASSESSED**: required evidence has not been collected. This is the current state of every gate.
- **PASS**: the predeclared criterion is met by documented observations.
- **CONDITIONAL**: limited support exists; name the missing evidence and a concrete follow-up experiment.
- **FAIL**: evidence contradicts the criterion; document the consequence and revise or stop the relevant assumption.

Choose thresholds to fit the actual user task before testing. Do not insert generic numeric cutoffs, manufacture pain records, or treat unknown data as proof of failure or success.

## Research outcome

Use [decision-memo-template.md](decision-memo-template.md). Link each conclusion to evidence or an experiment and distinguish technical feasibility, innovation, user value, business demand, and hackathon compliance.
