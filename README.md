# FireShield

Working repository for the Amazon **Build, Ship, Shape** developer hackathon, with **Fire TV** as the intended primary track.

## Current status

The repository contains the founder's **Synaptic Shield** research and a completed desk-research pressure test. The leading hypothesis is a publisher-controlled, release-aware scene-handling integration. Buyer demand, runtime behavior, and the application stack remain unvalidated. There is no runnable Fire TV application yet.

The current phase is research evaluation. The 6 October review recommends testing a narrow integration, with strong competitive counterevidence and explicit business and technical gates. The imported repositories support this research.

The name FireShield is the working repository name; it does not establish the product's purpose.

## Start here

- [VC pressure test](docs/research/Synaptic_Shield_VC_Pressure_Test_2026-10-06.md): corrected thesis, 29-source register, competitor/substitute analysis, economics, architecture risks and experiments.
- [Decision memo](docs/validation/synaptic-shield-decision-memo.md): verdict, riskiest assumptions and stop conditions.
- [Original supplied research](docs/research/source/Synaptic_Shield_Hardened_Idea_File_2026-09-28.md): preserved unchanged.
- [Project brief](docs/research/project-brief.md): current thesis and unresolved decisions.
- [Repository assessment](docs/research/repository-assessment.md): verified findings about proposed research tools.
- [Evidence register](docs/research/evidence-register.json): documented findings, counterevidence and unvalidated assumptions.
- [Validation plan](docs/validation/plan.md): test technical assumptions and business demand.
- [Fire TV validation workflow](docs/validation/fire-tv-workflow.md): apply the imported research templates and evidence gates.
- [Imported toolkit](third_party/idea-validation-agents/README.md): pinned, MIT-attributed MaxKmet templates.
- [Hackathon checklist](docs/hackathon/submission.md): prepare platform evidence and submission materials.
- [Prototype workspace](prototypes/README.md): define and compare small experiments.

## Immediate next step

The review proposes one bounded validation sprint: confirm a recurring publisher workflow and integration authority, and measure timing/release rejection on a selected Fire TV target using owned SDR VOD and harmless markers. Customer outreach and application implementation have not been started during this research assignment. See the decision memo for advance and stop criteria.

## Development approach

Validate a narrow user problem, demonstrate one useful end-to-end TV flow, and then expand only where evidence supports it. Record decisions and experiment outcomes in this repository.

Amazon's [multi-TV sample](https://github.com/AmazonAppDev/react-native-multi-tv-app-sample) is a candidate application foundation. It has not been imported, built, or tested here.

## Setup and operation

No dependency installation is required to review this documentation. Application setup and run commands will be added after the stack is selected.

No external services, API keys, deployments, research jobs, or scheduled background work are configured.

## Sources and reuse

MaxKmet's MIT-licensed research templates are included as references, with attribution and a pinned source manifest. See [third-party notices](THIRD_PARTY_NOTICES.md). The assessment documents each candidate's limitations. The license for this project's original code remains to be selected before an open-source submission.

