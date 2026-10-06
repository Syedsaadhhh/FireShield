# Research and business validation plan

Status: framework prepared; no customer experiments or prototype tests have been performed.

## Evidence discipline

Use the evidence register for external sources and experiment records for observed behavior. Label each claim as a fact, inference, hypothesis, or experiment result. Source popularity and generated agent agreement are not proof of demand.

For each important claim, collect both supporting and contradicting evidence. Identify what observation would cause the claim to be rejected or narrowed.

## Reverse check

| Question | Verification | Decision consequence |
| --- | --- | --- |
| Does an existing product or built-in Amazon feature already solve this? | Compare the exact user task, supported platform, constraints, and pricing using primary sources | Identify a specific gap or reconsider the concept |
| Is the claimed Fire TV capability actually available? | Check official APIs and demonstrate it on the chosen platform | Remove unsupported assumptions from the prototype |
| Does the problem recur and materially affect users? | Anonymized accounts of recent incidents and current workarounds | Define a concrete audience and problem frequency |
| Does the proposed solution improve that task? | Observe task completion against the current workaround | Keep, revise, or abandon the proposed flow |
| Can a simpler implementation deliver the same value? | Compare a small baseline against the proposed complex feature | Choose complexity only where it provides observed benefit |
| Are performance and reliability claims credible? | Measure actual behavior, errors, and recovery in the target environment | Document constraints and fix critical failures |

## Business checks

| Area | Evidence to collect | What remains a hypothesis until tested |
| --- | --- | --- |
| User and buyer | Who has the problem, who controls adoption, and who pays | User interest means purchase intent |
| Urgency | Recent examples, frequency, and cost of the workaround | A broad problem statement means demand |
| Value | What outcome improves and whether users notice it | A novel feature creates meaningful value |
| Willingness to pay | Specific commitments or a realistic pricing experiment | Likes, compliments, or hypothetical survey answers establish revenue |
| Distribution | An achievable way to reach the audience, including Appstore discovery where relevant | Publishing alone supplies customers |
| Unit economics | Measured usage and current provider/storage costs for the selected design | Free development services will remain free at scale |
| Retention | Reasons to repeat the core task and actual repeat use when measurable | A successful demo implies regular use |

Do not contact interviewees, send messages, or run paid campaigns without explicit user instructions.

## Experiment record

Create one Markdown record per experiment with:

- Experiment ID, date, owner, and relevant prototype.
- Hypothesis and the strongest competing explanation.
- User task and comparison baseline.
- Test method, participant/sample context, and consent where relevant.
- Success and failure thresholds chosen before the test; justify them for the task.
- Actual observations, measured results, and source/evidence links.
- Limitations and unresolved questions.
- Decision: keep, revise, abandon, or insufficient evidence.
- Next action and the observation that would change the decision.

## Progression gates

1. Research intake: understand the supplied concept and its evidence.
2. Feasibility: demonstrate the riskiest supported device interaction.
3. Value: demonstrate one complete flow that improves a defined user task.
4. Business: distinguish a credible demand signal from untested market claims.
5. Submission: record the app on the required device/simulator, supply reproducible code, and complete the feedback and demo materials.

These are workflow gates, not claims that evidence already exists.
