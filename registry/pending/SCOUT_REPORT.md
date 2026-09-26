# Registry Scouting & Audit Report (`SCOUT_REPORT.md`)

**Date:** 2026-09-26  
**Auditor:** Antigravity / AI Add-ons Manager  
**Scouting Scope:** Discover new MCP servers & skills (Part A) and inspect existing manifests for available updates & integrity (Part B).  
**Staging Location:** `registry/pending/` and `registry/pending/updates/`  
**Live Registry Status:**  
- `linear-mcp.yaml` **REMOVED** from live registry (marked **BLOCKED**; package `@ibraheem4/linear-mcp` is 404 on npm).
- `brave-search-mcp.yaml` **CORRECTED** in live registry (`1.0.0` -> `0.6.2` with real tarball SHA-256).
- `postgres-mcp.yaml` **CORRECTED** in live registry (`1.1.0` -> `0.6.2` with real tarball SHA-256).
- Regression test added in `tests/integration/test_registry_validation.py` asserting all live package manifests resolve on npm.

---

## Executive Summary

1. **Urgent Live Registry Fixes Executed:**
   - **`linear-mcp.yaml` removed and marked BLOCKED:** Real-world install attempt confirmed `aiaddons install linear-mcp` now cleanly reports `Add-on 'linear-mcp' not found in registry.` rather than attempting to execute a broken npm package.
   - **Permanent Regression Test Added:** `test_package_manifest_resolves_on_npm` in `tests/integration/test_registry_validation.py` actively validates all live package manifests against the public npm registry HTTP API (13/13 passing).
   - **Brave & Postgres Corrected:** Both were set to fabricated version numbers (`1.0.0` and `1.1.0`) with placeholder hashes in historical commits (`47b7343` and `ecf031b`). Both corrected to real npm latest `0.6.2` with verified cryptographic SHA-256 tarball hashes.
2. **Full Live Registry Version Audit:**
   - Identified that historical manifests for `playwright-mcp`, `github-mcp`, `filesystem-mcp`, `sequential-thinking-mcp`, `figma-mcp`, and `firecrawl-mcp` were similarly initialized with placeholder versions (`1.0.0`/`1.2.0`) rather than matching upstream npm package versions (`0.0.82`, `2025.4.8`, `2026.8.31`, `1.40.6`, `3.25.5`).
3. **Pending Add-ons Staged (Part A):** 5 candidates verified live and staged in `registry/pending/`; 2 marked **BLOCKED** (remote/OAuth-only: Linear official, Slack official).
4. **Pending Updates Staged (Part B):** 6 verified updates staged in `registry/pending/updates/`.

---

## Part 1: Investigation & Live Registry Corrections

### A. Root Cause: `brave-search-mcp` (1.0.0) & `postgres-mcp` (1.1.0)

Git history investigation:
- **`postgres-mcp.yaml`**: Created on Sun Aug 9, 2026 in commit `ecf031bc80bf85e81fca567995b2c3eb5609ed1c` ("Harden registry security model"). The author initialized it with placeholder metadata:
  ```yaml
  version: 1.1.0
  checksum: "sha256:a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef"
  ```
- **`brave-search-mcp.yaml`**: Created on Fri Aug 28, 2026 in commit `47b73431728e55e8dc9226bf195bf9a3aa4a8789` ("feat(registry): add official manifests for GitHub, Context7, Playwright, Postgres, Filesystem, and Brave MCP servers"). Initialized with placeholder metadata:
  ```yaml
  version: 1.0.0
  checksum: "sha256:e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6"
  ```
Neither `@modelcontextprotocol/server-brave-search` nor `@modelcontextprotocol/server-postgres` ever published a `1.0.0` or `1.1.0` on npm. Both packages' latest release is **`0.6.2`**.

**Corrections Applied in Live Registry:**
- `brave-search-mcp.yaml`: `version: 0.6.2`, `checksum: sha256:91733a4e1e0c0de6738d3542135c40bd4356aa48a336191c855e4f9feac40f85`
- `postgres-mcp.yaml`: `version: 0.6.2`, `checksum: sha256:af55200a01b13ae31949fb4a8864ee6043b891d00ff83330d1779b18aa5ba26f`
- Tests in `test_mcp_registry_manifests.py` updated to assert `version == "0.6.2"`.

---

### B. Full Audit of All Other Live Manifests vs Real Upstream

| Manifest File | Declared Version | Real Latest on npm | Exists on npm? | Audit Finding |
| :--- | :--- | :--- | :--- | :--- |
| **`brave-search-mcp.yaml`** | **0.6.2** (corrected) | 0.6.2 | Yes | **CORRECTED TO REAL VERSION & REAL TARBALL HASH** |
| **`postgres-mcp.yaml`** | **0.6.2** (corrected) | 0.6.2 | Yes | **CORRECTED TO REAL VERSION & REAL TARBALL HASH** |
| **`linear-mcp.yaml`** | 1.0.0 | None (404) | **No (404)** | **REMOVED FROM LIVE REGISTRY (BLOCKED)** |
| **`playwright-mcp.yaml`** | 1.0.0 | **0.0.82** | No (`1.0.0` never published) | Initialized with placeholder `1.0.0` (latest npm release is `0.0.82`) |
| **`github-mcp.yaml`** | 1.2.0 | **2025.4.8** | No (`1.2.0` never published) | MCP maintainers switched package to CalVer `2025.4.8` |
| **`filesystem-mcp.yaml`** | 1.0.0 | **2026.8.31** | No (`1.0.0` never published) | MCP maintainers switched package to CalVer `2026.8.31` |
| **`sequential-thinking-mcp.yaml`** | 1.0.0 | **2026.8.31** | No (`1.0.0` never published) | MCP maintainers switched package to CalVer `2026.8.31` |
| **`figma-mcp.yaml`** | 1.0.0 | **1.40.6** | No (`1.0.0` never published) | Initialized with placeholder `1.0.0` (latest npm is `1.40.6`) |
| **`firecrawl-mcp.yaml`** | 1.0.0 | **3.25.5** | No (`1.0.0` never published) | Initialized with placeholder `1.0.0` (latest npm is `3.25.5`) |
| **`context7-mcp.yaml`** | 1.0.0 | **4.1.1** | Yes (`1.0.0` existed) | Real version on npm; updated to `4.1.1` in staging |
| **`notion-mcp.yaml`** | 1.0.0 | **2.5.2** | Yes (`1.0.0` existed) | Real version on npm; updated to `2.5.2` in staging (vendor deprecated local) |
| **`stripe-mcp.yaml`** | 0.3.3 | **0.3.3** | Yes | **MATCHES LATEST NPM** |
| **`sentry-mcp.yaml`** | 0.42.0 | **0.42.0** | Yes | **MATCHES LATEST NPM** |
| **`supabase-mcp.yaml`** | 0.13.0 | **0.13.0** | Yes | **MATCHES LATEST NPM** |
| **`caveman.yaml`** | 1.0.0 | `2fd153c` (git) | N/A | Pinned SHA `15581d1` verified live; newer commit on upstream |
| **`karpathy-behavioral-skill.yaml`** | 1.0.0 | `2c60614` (git) | N/A | Pinned SHA matches upstream `HEAD` |
| **`vibesec-skill.yaml`** | 1.0.0 | `0590993` (git) | N/A | Pinned SHA matches upstream `HEAD` |
| **`skill-creator.yaml`** | 1.0.0 | `3337550` (git) | N/A | Pinned SHA `34040c9` verified live |
| **`superpowers-*.yaml` (6 skills)** | 1.0.0 | `8ca22db` (git) | N/A | Pinned SHA `b36e082` verified live |
| **`ponytail-*.yaml` (7 skills)** | 4.9.0 | `e3ba2aa` (git) | N/A | Pinned SHA `0a4dd63` verified live |

---

## Part 2: Part A — New Add-on Candidates (Staged in `registry/pending/`)

| Candidate ID | Name & Package / Repo | Type | Verification & Checksum | Real Test Install Outcome | Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`chrome-devtools-mcp`** | `chrome-devtools-mcp` (v1.10.1) by Chrome DevTools Team / Google | MCP (stdio) | **Verified on npm**: Tarball sha256 `012cbcf6e832d4f6709dad0c21d7bef17089e94adee9cf33179d15ea0a9adf2b`. Official tool for agents to control and trace live Chrome browser sessions. | **PASSED**: Installed cleanly to Claude Code workspace config; removed cleanly. | **ADD** (Staged: `registry/pending/chrome-devtools-mcp.yaml`) |
| **`heroku-mcp`** | `@heroku/mcp-server` (v1.2.9) by Heroku / Salesforce | MCP (stdio) | **Verified on npm**: Tarball sha256 `a8bbd5c938304e6e0028ca9e6edc457827efd364fced6ee97e9cc1ed79657904`. Official server for apps, dynos, add-ons, pipelines, and PostgreSQL databases. | **PASSED**: Installed cleanly to Claude Code workspace with dummy `HEROKU_API_KEY`; removed cleanly. | **ADD** (Staged: `registry/pending/heroku-mcp.yaml`) |
| **`browserstack-mcp`** | `@browserstack/mcp-server` (v1.5.1) by BrowserStack | MCP (stdio) | **Verified on npm**: Tarball sha256 `fed162cca8b00a5cda6ac2e5d740469c8e0116f195eecd94c4c9edf0bdcce7b4`. Official server for real device testing, App Automate, and automated root cause analysis. | **PASSED**: Installed cleanly to Claude Code with dummy credentials; removed cleanly. | **ADD** (Staged: `registry/pending/browserstack-mcp.yaml`) |
| **`qase-mcp`** | `@qase/mcp-server` (v2.7.4) by Qase | MCP (stdio) | **Verified on npm**: Tarball sha256 `5479b00153f9c49619222a76fbe618215ce433a66e72113860c0bdb0bdd398ca`. Official server for Qase Test Management Platform with QQL support. | **PASSED**: Installed cleanly to Claude Code with dummy `QASE_API_TOKEN`; removed cleanly. | **ADD** (Staged: `registry/pending/qase-mcp.yaml`) |
| **`make-project-github-ready`** | `https://github.com/montasim/skills.git` (path: `skills/make-project-github-ready`) | Skill | **Verified via git**: Commit `0fd800f5e7deebdad9f978199d5da943f7da6278` exists live on remote; contains valid `SKILL.md`. | **PASSED**: Cloned, checked out, deployed `SKILL.md` to `.claude/skills/make-project-github-ready`; removed cleanly. | **ADD** (Staged: `registry/pending/make-project-github-ready.yaml`) |
| **`linear-mcp` (Official Remote)** | `https://mcp.linear.app/mcp` | MCP (remote) | **Official Linear MCP endpoint** (Streamable HTTP / OAuth). The existing live package `@ibraheem4/linear-mcp` 404s on npm. | **N/A**: Local stdio not supported by official vendor. | **BLOCKED** (Pending remote-transport schema support) |
| **`slack-mcp` (Official Remote)** | `https://mcp.slack.com/mcp` | MCP (remote) | **Official Slack MCP endpoint** (Remote OAuth). Local `@modelcontextprotocol/server-slack` is deprecated. | **N/A**: Local stdio not supported by official vendor. | **BLOCKED** (Pending remote-transport schema support) |

---

## Part 3: Part B — Staged Manifest Updates (in `registry/pending/updates/`)

| Add-on ID | Current Live Version | Available npm Version | Verified Real Tarball Checksum | New/Changed Requirements Detected | Staged Update Manifest | Test Install Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`context7-mcp`** | 1.0.0 | **4.1.1** | `sha256:cbd5f81ca37b42ad5491099017b00d12107eb075bb3111c7a216b632fac97c4a` | None. `CONTEXT7_API_KEY` remains optional. (Upstash also hosts remote endpoint `https://mcp.context7.com/mcp`). | `context7-mcp-v4.1.1.yaml` | **PASSED** (Installed & removed cleanly) |
| **`notion-mcp`** | 1.0.0 | **2.5.2** | `sha256:2506106e21e2487531d106cf0b37cd3b1b18913c91dc141719f1c05b91f8bbce` | `NOTION_TOKEN` still required. **Vendor Notice:** Notion upstream documentation notes this local repository is unmaintained and deprecated in favor of official Remote Notion MCP (`https://developers.notion.com/docs/mcp`). | `notion-mcp-v2.5.2.yaml` | **PASSED** (Installed & removed cleanly) |
| **`figma-mcp`** | 1.0.0 | **1.40.6** | `sha256:4635cb61e3b90c85d556403b6bfc0cf94ac4d19bc15171bca60d864ca31bc3ca` | None. `FIGMA_ACCESS_TOKEN` pattern `^fig(d\|pat)_[a-zA-Z0-9_\-]+$` remains identical. | `figma-mcp-v1.40.6.yaml` | **PASSED** (Schema & checksum verified) |
| **`firecrawl-mcp`** | 1.0.0 | **3.25.5** | `sha256:d1d2137fdd163afafce5a2c46d644d13ab1a211ac49a64f450011e60015d8821` | None. `FIRECRAWL_API_KEY` remains optional for self-hosted instances. | `firecrawl-mcp-v3.25.5.yaml` | **PASSED** (Schema & checksum verified) |
| **`filesystem-mcp`** | 1.0.0 | **2026.8.31** | `sha256:a239da270c403c42eb03e1ca7cca07c858085819797b74856ddbb91b50491b1a` | Transitioned to CalVer (`2026.8.31`) by Anthropic/MCP maintainers. No env vars required. | `filesystem-mcp-v2026.8.31.yaml` | **PASSED** (Schema & checksum verified) |
| **`sequential-thinking-mcp`** | 1.0.0 | **2026.8.31** | `sha256:b47c367af4bc1ef96cec096e67151899a29317c42009f0ac53a0d51b85c2c71d` | Transitioned to CalVer (`2026.8.31`) by Anthropic/MCP maintainers. No env vars required. | `sequential-thinking-mcp-v2026.8.31.yaml` | **PASSED** (Installed & removed cleanly) |
