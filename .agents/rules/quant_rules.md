# Quant System Master Rules & Directives

## 1. Mandatory User Review Before Commits
- ALWAYS present proposed code changes, file lists, and diffs to the USER for review before executing any `git commit` or `git push` commands.
- Do not commit or push changes without explicit user approval.

## 2. No Status Polling Loops
- Do not poll running background tasks with repetitive status checks; wait for reactive notifications or user input.

## 3. Strict Step-Gated Multi-Repo Push Order
When pushing updates across multiple repositories, you MUST strictly adhere to the following blocking sequence:

- **Gate 1 (`common-lib` First)**:
  1. Push `common-lib` to `develop`.
  2. **BLOCK & WAIT**: You are STRICTLY FORBIDDEN from committing or pushing any other repository until the GitHub Actions CI/CD runner on Synology NAS completes and `common-lib` is confirmed merged into `master`.
- **Gate 2 (Dependent Backend Microservices)**:
  1. Only after `common-lib` is merged into `master`, push dependent backend services (`gexdex-api`, `quant-level-pipeline`, `mm-dex-gex-pipeline`, `discord-quant-bot`, etc.).
  2. Wait for their deployments to succeed.
- **Gate 3 (`quant-pwa` Last)**:
  1. `quant-pwa` MUST ALWAYS be pushed last after all backend services are fully deployed and healthy.

Before executing any deployment, you MUST output the explicit **Step-Gating Checklist** in chat and check off each gate only after it is physically verified.

## 4. Project Documentation & Continuous Maintenance
- Every project folder must maintain up-to-date documentation (`README.md` or architecture guide) documenting how it works and all design goals.
- Whenever any code/config is updated in a project folder, revise its documentation accordingly.
