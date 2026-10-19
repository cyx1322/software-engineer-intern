# Tool usage record

Complete the table before submission. Include AI assistants, search engines, documentation sources, and open-source code that influenced the implementation. Replace the example rows and add rows as needed.

| Tool or source | Use | Files materially influenced |
|---|---|---|
| Claude Code (Claude Opus 5.5) | Generated the Module 1 API, tests, and schema documentation from my design decisions (AI_LOG interaction 1). Measured the mock services, generated the Module 2 OCR, export, and delivery code and tests, and ran the recovery and delivery checks (interaction 2). Generated the Q3.1 and Q3.2 deployment files, readiness, and seeding, and ran the deployment checks (interaction 3). Generated the backup script, smoke test, and runbook, and tested each procedure (interaction 4). Generated the GitHub Actions CI workflow and checked it locally (interaction 5). | `q1_api/app/`, `q1_api/tests/`, `q1_api/README.md`, `q2_integration/README.md`, `q3_deploy/`, `.github/workflows/ci.yml`, `REFLECTION.md` |
| [Documentation, search engine, or open-source source] | [Information or code used] | [File paths, or `Not applicable`] |

Record AI interactions that influenced design or generated code in [AI_LOG.md](AI_LOG.md).
