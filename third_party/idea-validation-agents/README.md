# Idea-validation reference templates

Imported from [MaxKmet/idea-validation-agents](https://github.com/MaxKmet/idea-validation-agents) at commit `3a4c800a3022e73e9997533b603c79b60ee7597a` on 2026-10-06.

The canonical skill, workflow, and memory documentation is copied unchanged. See [UPSTREAM.json](UPSTREAM.json) for paths and original Git blob hashes, and [LICENSE](LICENSE) for the upstream MIT license and copyright notice.

## Included

- Fifteen canonical skill documents, including competition, pricing, acquisition cost, distribution, retention, risk, scoring, and decision memos.
- Four workflow templates and their index.
- Five trend-research prompt templates.
- Memory structure guidance and the core-desires reference.

## Use in FireShield

Begin with [FireShield's adaptation guide](../../docs/validation/fire-tv-workflow.md), then read the relevant upstream template as source material.

These files are references for a capable AI assistant. No software engine, autonomous agents, external data connectors, or scheduler is installed by this import. The upstream root agent instructions and assistant configuration have not been installed into FireShield.

The upstream `memory/ideas/<slug>/` paths describe output conventions; they do not contain completed research. Store this project's actual findings and experiments under the existing research and validation directories.

Known issues include mobile/B2C assumptions, heuristic numerical benchmarks, incomplete rubrics, undocumented inputs, and a six-versus-seven-dimension scoring inconsistency. The adaptation guide documents how to handle them before making a product decision.
