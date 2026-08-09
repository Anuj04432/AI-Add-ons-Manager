# AGENTS.md - Engineering Rules & Guidelines for AI Add-ons Manager (`aiaddons`)

This document defines the mandatory engineering standards, architectural constraints, and coding guidelines for AI Add-ons Manager (`aiaddons`). All developers and AI coding agents working on this project MUST strictly adhere to these rules.

---

## 1. Project Structure Standards
- Use the standard **`src/` layout** (PEP 621): `src/aiaddons/`.
- Maintain strict modularity across subpackages:
  - `cli/`: Non-interactive CLI commands (Typer + Rich).
  - `tui/`: Textual interactive terminal UI screens, widgets, and app controllers.
  - `core/`: Pure domain models, compatibility resolver, and transactional installer logic.
  - `agents/`: Agent detection and adapter implementations (Claude Code, Codex).
  - `integrations/`: Integration type handlers (MCP, Skill, Plugin, CLI Tool).
  - `registry/`: Registry fetching, schema validation, and security sanitization.
  - `state/`: Local installation state database and `aiaddons.lock` lockfile management.
- Tests must be organized under `tests/unit/`, `tests/integration/`, and `tests/fixtures/`.

---

## 2. Modular Architecture & Abstraction
- Use Python `Protocol` or `ABC` interfaces for all extensible components (`BaseAgentAdapter`, `BaseIntegrationHandler`).
- The domain core (`core/`) MUST NOT import or depend on UI components (`tui/`, `cli/`).
- Adapters and Handlers must be completely decoupled from presentation logic. They take data models as input and return typed result objects (`ActionResult`, `AgentDetectionResult`).

---

## 3. Registry-Driven Design (No Hard-coded Add-ons)
- **CRITICAL**: DO NOT hard-code individual add-ons, MCP servers, or skills into Python code or classes.
- Integrations MUST be defined dynamically as JSON/YAML metadata conforming to `IntegrationManifest`.
- Adding a new integration to the ecosystem must require ONLY adding a manifest entry to a registry—NEVER modifying Python source code.

---

## 4. Agent Adapter Constraints (Claude Code & Codex)
- All agent adapters must implement `BaseAgentAdapter`.
- Adapters must safely handle both **Global** scope (`~/.claude.json`, `~/.codex/`) and **Workspace** scope (`.claude/`, `.agents/`).
- File modifications to agent configurations MUST be atomic (read -> update dict -> write to temporary file -> atomic rename).
- Adapter detection must check both executable presence in `PATH` (`shutil.which`) and configuration directory existence.

---

## 5. MCP and Skills Integration Guidelines
- **MCP Servers**:
  - MCP handlers build and inject `mcpServers` configuration objects into agent config files.
  - MCP servers must NEVER be launched directly by `aiaddons` as unmanaged background daemons.
  - Environment variables required by MCP servers must be clearly declared with `required` and `secret` flags.
- **Agent Skills**:
  - Skills must be validated against `SKILL.md` frontmatter rules before installation.
  - Skill deployment must safely copy/link directory contents into agent-specific skill locations (`.claude/skills/`, `.agents/skills/`).

---

## 6. Security & Command Execution Guardrails
- **No Arbitrary Shell Execution**:
  - NEVER execute arbitrary shell commands or scripts specified in registry metadata.
  - Subprocess calls MUST NEVER use `shell=True`. All invocations must use explicit argument lists: `["npx", "-y", "@scope/package"]`.
- **Executable Whitelist**:
  - Only pre-approved runtime binaries (`npx`, `uvx`, `python`, `node`, `pip`, `git`) listed in `allowed_executables` may be executed.
- **Command Sanitization**:
  - Reject any registry manifest containing shell operators: `;`, `&&`, `||`, `|`, `>`, `<`, `$()`, backticks, or environment variable expansions.
- **Checksum Verification**:
  - Downloads (zip archives, git bundles) MUST be validated against cryptographic SHA-256 hashes defined in the manifest.
- **Secret Protection**:
  - Sensitive inputs (API keys, personal access tokens) MUST be flagged `secret: true`, masked in UI/logs, and stored safely.

---

## 7. Dependency Management & Packaging
- Managed via `pyproject.toml` using `hatchling` as the build backend.
- Core runtime dependencies must remain minimal:
  - `typer` (CLI)
  - `rich` (Formatting)
  - `textual` (TUI)
  - `pydantic` v2 (Validation & Schemas)
  - `httpx` (Async HTTP)
  - `packaging` (SemVer checks)
- Avoid adding unnecessary external packages. Favor standard library (`pathlib`, `json`, `shutil`, `typing`, `hashlib`, `subprocess`).

---

## 8. Type Hints & Code Quality
- Strict static typing is required everywhere. All function parameters and return values must have explicit type annotations.
- Use Python 3.11+ type syntax (`list[str]`, `dict[str, Any]`, `str | None`).
- No implicit `Any` types in domain models.
- Core code must pass static type checking without errors (`mypy --strict`).

---

## 9. Error Handling & Transactional Rollbacks
- Create a dedicated exception hierarchy inheriting from `AIAddonsError`:
  - `RegistryFetchError`
  - `ManifestValidationError`
  - `IncompatibleAgentError`
  - `SecurityValidationError`
  - `InstallationError`
- Multi-step installations must use an `InstallPlan` with a `RollbackStack`. If step $N$ fails, steps $N-1$ down to $1$ must execute their `undo()` handler to leave the host system clean.
- Never swallow exceptions silently. All errors reported to the user in CLI/TUI must be clear, actionable, and user-friendly.

---

## 10. Logging & Auditing
- Use structured logging via standard `logging`.
- Log files must be stored in the app data directory (`~/.aiaddons/logs/aiaddons.log`).
- **NEVER** write plain-text secrets, passwords, or tokens to log files. Filter or mask all secret values.

---

## 11. Testing Requirements
- Unit tests (`pytest`) are required for:
  - Manifest parsing and Pydantic schema validation.
  - SemVer and OS compatibility resolution.
  - Security command sanitizer and whitelist verifier.
  - JSON configuration diffing and atomic updates.
- Mock all file system writes and subprocess executions in unit tests using `pytest` fixtures (`tmp_path`, `monkeypatch`, `unittest.mock`).
- Use Textual's `App.run_test()` harness for UI testing.
