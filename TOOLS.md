# Tool usage record

| Tool or source | Use | Files materially influenced |
|---|---|---|
| Claude Code (Claude Opus 5.5) | Generated the Module 1 API, tests, and schema documentation from my design decisions (AI_LOG interaction 1). Measured the mock services, generated the Module 2 OCR, export, and delivery code and tests, and ran the recovery and delivery checks (interaction 2). Generated the Q3.1 and Q3.2 deployment files, readiness, and seeding, and ran the deployment checks (interaction 3). Generated the backup script, smoke test, and runbook, and tested each procedure (interaction 4). Generated the GitHub Actions CI workflow and checked it locally (interaction 5). | `q1_api/app/`, `q1_api/tests/`, `q1_api/README.md`, `q2_integration/README.md`, `q3_deploy/`, `.github/workflows/ci.yml`, `REFLECTION.md` |
| GitHub Docs: [Publishing and installing a package with GitHub Actions](https://docs.github.com/en/packages/managing-github-packages-using-github-actions-workflows/publishing-and-installing-a-package-with-github-actions) | The `packages: write` permission for `GITHUB_TOKEN`, and that a package created by a workflow inherits the repository's visibility | `.github/workflows/ci.yml` |
| `action.yml` of `actions/checkout`, `actions/setup-python`, and `docker/login-action` (latest releases) | The current major versions, and the inputs each version accepts | `.github/workflows/ci.yml` |

AI interactions that influenced design or generated code are recorded in [AI_LOG.md](AI_LOG.md).
