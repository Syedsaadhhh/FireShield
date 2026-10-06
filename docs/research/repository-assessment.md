# Proposed research-tool assessment

Reviewed on 2026-10-06 using authenticated GitHub metadata and source inspection. This is a static review; no application was installed or executed and no test suite was run.

## 1. sachinsapkotadev/AI-IDEA-RESEARCHER

Repository: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER

Reviewed source commit: 99e12dcd233b92ec26fce9a9402a2804f906d915.

GitHub metadata reported a public repository, no detected license, 2 stars, and 0 forks at review time. These counts do not establish software quality or a ranking.

### Useful implementation

- A FastAPI backend, PostgreSQL models/migrations, and an Astro dashboard are present.
- A five-agent pipeline covers markets, competitors, ideas, technical feasibility, and validation planning.
- Market prompts request source references, avoid invented market-size figures, and ask for marked hypotheses.
- Research extraction, report generation, deployment configuration, and mocked tests are present.

These are implementation observations, not proof that the application works end to end.

### Concrete limitations

| Finding | Source | Implication |
| --- | --- | --- |
| The README says private/all rights reserved, although GitHub currently reports public visibility; there is no license file in the reviewed tree | README.md and repository metadata | Do not assume permissive reuse terms from visibility; seek clear permission before copying code into the project |
| The deep-research worker references an undefined topic variable | Backend/app/deep_research/engine.py, worker at line 108 and use at line 128 | The worker cannot proceed through this path as written |
| Deep research calls ResearchService.search(), but the reviewed service exposes run() rather than search() | Backend/app/deep_research/engine.py and Backend/app/research/service.py | Repair the interface integration before relying on the loop |
| The engine constructs OpenRouterClient(provider=...) and calls chat(); the reviewed client accepts key_pool and exposes chat_completion() | Backend/app/deep_research/engine.py and Backend/app/ai/client.py | Additional client-interface blockers exist |
| Automation explicitly requires an unimplemented backend scheduler | Website/src/pages/dashboard/automation.astro | The dashboard does not provide scheduled continuous research |
| Deep-research job state is kept in a process-local dictionary | Backend/app/deep_research/engine.py | A restart loses tracking state; reliable jobs need durable execution and storage |
| Frontend authentication remains TODO code | Website/src/lib/auth/index.ts | Deployment needs a separate authentication and access-control review |
| Android contains only a README | Reviewed Git tree | No implemented Fire TV application foundation is present |
| ValidationPlanner generates interviews, landing-page tests, and prototype-test plans | Backend/app/agents/validation.py | Reports plan validation; users still need to perform the experiments |
| Tests mock external APIs according to the README | README.md | Passing mocked tests would not establish real provider integration or customer demand |

Source links:
- README: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER/blob/99e12dcd233b92ec26fce9a9402a2804f906d915/README.md
- Deep research: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER/blob/99e12dcd233b92ec26fce9a9402a2804f906d915/Backend/app/deep_research/engine.py
- Research service: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER/blob/99e12dcd233b92ec26fce9a9402a2804f906d915/Backend/app/research/service.py
- AI client: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER/blob/99e12dcd233b92ec26fce9a9402a2804f906d915/Backend/app/ai/client.py
- Automation: https://github.com/sachinsapkotadev/AI-IDEA-RESEARCHER/blob/99e12dcd233b92ec26fce9a9402a2804f906d915/Website/src/pages/dashboard/automation.astro

### Verdict

Keep as a reference for research-workflow design. Do not adopt it as the current project foundation. Its usefulness would need to outweigh repair effort, provider requirements, hosting costs, and unresolved reuse terms.

The screenshot's suggestion that a fork unlocks a separate private dashboard is not established by the inspected repository. The dashboard source is already in the visible Website directory.

## 2. kfros/saas-validation-starter

Requested URL: https://github.com/kfros/saas-validation-starter

The exact repository returned GitHub 404. Public repository searches and web searches did not identify a verified matching project. This does not prove it never existed: it may be private, renamed, deleted, or incorrectly linked.

Verdict: unavailable for assessment. Do not fork or base the project on an unverified identity. Revisit if a working URL is supplied.

## 3. saas-starter/saas-validation

Requested URL: https://github.com/saas-starter/saas-validation

Checked again on 2026-10-06 after the user's follow-up recommendation. The exact GitHub metadata request returned 404; repository-scoped search also reported the resource unavailable/nonexistent or inaccessible.

No source was available to substantiate the claimed evidence gates, pain-mining, or skeptic track. The name alone does not establish an identical replacement.

Verdict: do not adopt an unverified repository.

## 4. MaxKmet/idea-validation-agents

Repository: https://github.com/MaxKmet/idea-validation-agents

Reviewed source commit: 3a4c800a3022e73e9997533b603c79b60ee7597a. GitHub reported a public, MIT-licensed repository with 473 stars at review time. License content and the reviewed source tree were inspected directly.

### What is present

- Fifteen canonical skill documents for competition, pricing, distribution, acquisition economics, retention, markets, scoring, and decision-making.
- Four workflow templates and prompt templates for trend research.
- Assistant adapters for Claude Code, Codex, and Cursor.
- Structured output conventions, risk experiments, and decision memo templates.

This is a workflow/prompt repository for existing AI tools. Its templates do not themselves supply a standalone service, verified customer data, a running research job, or a Fire TV app.

### Reverse-check findings

- There is no hypothesis.yaml, evidence/verified_pains.jsonl, or G1-G6 gate implementation in the reviewed tree. The claim of identical logic to unavailable kfros files cannot be verified.
- The focus is indie B2C/mobile apps; several research methods need adaptation for TV households, Amazon Appstore, and device-specific constraints.
- The pricing template estimates price-sensitivity thresholds from competitor prices; it does not include observed customer survey responses.
- Numerical heuristics and generic benchmarks need evidence or scenario labels.
- The scoring document lists six weights while its confidence/missing-input guidance refers to seven dimensions.
- Several canonical rubrics are incomplete/TODO.
- The validation chain references user_extraction.json and weighted_signals.json without specifying their generation inside that chain.
- Assertions that judges love it or that it is an identical replacement are not substantiated by the inspected code.

### Verdict and import

Worth using as a research-template reference with these limitations documented. FireShield now contains 30 unmodified, MIT-attributed source documents, a pinned provenance manifest, and an original Fire TV adaptation guide. Root upstream assistant instructions/configuration have not been installed.

See [Fire TV adaptation](../validation/fire-tv-workflow.md), [third-party references](../../third_party/idea-validation-agents/README.md), and [license notice](../../THIRD_PARTY_NOTICES.md).

## Interpreting the earlier 404

The earlier error was GitHub API 404 on repository metadata lookup. No fork action was attempted. A 404 by itself cannot identify missing OAuth repo scope: unavailable/private resources, access, or URL issues can yield that response. GitHub App permissions and OAuth scopes also have different authentication models.

Official explanation: https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api

The connection successfully committed and verified FireShield files, so the unavailable third-party URLs do not establish a general inability to write to the user's repositories.

An AI-generated reconstruction offered without the original source, commit, and license should be labeled a new template. It cannot be treated as recovered exact kfros files.

## Application-foundation candidate

AmazonAppDev/react-native-multi-tv-app-sample is linked by the organizers as a Fire TV starter:
https://amazonappdev2026.devpost.com/updates/46232-the-amazon-devices-build-session-recording-is-up-resources

Its README describes Fire OS and Vega variants, shared UI, video playback, and remote navigation:
https://github.com/AmazonAppDev/react-native-multi-tv-app-sample

It is a candidate for application development after reviewing the user's researched concept. Its README lists MIT-0 licensing. It has not been imported, built, or tested here. Verify its license, dependencies, and target-platform behavior before adopting it.

## Actions taken

- No third-party repositories were forked.
- MIT-licensed MaxKmet research templates were imported with attribution and source hashes; no material from the unavailable or rights-reserved research repositories was copied.
- FireShield was initialized with original project planning documents.
