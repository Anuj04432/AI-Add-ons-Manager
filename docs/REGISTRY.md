# AI Add-ons Manager (`aiaddons`) — Registry & Metadata System Specification

## 1. Overview & Core Philosophy

The `aiaddons` registry is a **declarative, static, metadata-driven repository** of AI coding agent add-ons, MCP servers, agent skills, plugins, and CLI tools.

### Core Principles
1. **Declarative Specification**: Manifests describe *WHAT* an add-on is, *WHERE* its source is located, and *WHAT* dependencies/capabilities it requires—never *HOW* to run arbitrary shell scripts or CLI commands.
2. **Strict Typed Validation (`extra = "forbid"`)**: Every registry file (`.yaml`, `.yml`, `.json`) is parsed and validated against strict Pydantic v2 domain schemas (`IntegrationManifest`, `MCPHandlerSpec`, `SkillHandlerSpec`, `PluginHandlerSpec`). Unexpected or untyped fields (`command`, `args`, `script`, `install_command`) trigger immediate validation errors.
3. **Primary Security via Typed Handlers & Runtimes**: The registry forbids arbitrary command execution by enforcing an explicit allowlist of supported runtimes (`npx`, `uvx`, `node`, `python`) inside `MCPHandlerSpec`. Trusted application code constructs execution commands safely; manifest authors cannot specify arbitrary binaries or CLI flags.
4. **Path Traversal Protection**: Relative path fields (`source.path`, `skill_file`, `supporting_files`) are strictly validated using `validate_safe_relative_path`, rejecting absolute paths, UNC network paths, Windows drive letters, and traversal sequences (`..`).
5. **Cryptographic Source Pinning**: Remote sources must specify cryptographic pins (`commit_sha` for Git, SHA-256 `checksum` for remote archives/packages) before being marked as installable.
6. **Application-Derived Trust**: Manifests cannot self-assert verified trust status. `verification_status` defaults to `UNVERIFIED` on loaded manifests and can only be set to `VERIFIED` via out-of-band application verification against a signed index.

---

## 2. Registry File Formats (YAML & JSON)

Registry entries can be authored in either **YAML** (`.yaml` / `.yml`) or **JSON** (`.json`). YAML is recommended for human-authored registry manifests.

### Directory Structure
```text
registry/
└── addons/
    ├── github-mcp.yaml
    ├── postgres-mcp.yaml
    ├── python-lint-plugin.yaml
    └── refactoring-skill.yaml
```

---

## 3. Manifest Schema Specification

Every registry manifest must conform to `IntegrationManifest`:

```yaml
id: github-mcp
name: GitHub MCP Server
version: 1.2.0
description: Model Context Protocol server for searching code, managing PRs, and inspecting issues.
documentation_url: https://github.com/modelcontextprotocol/servers
license: MIT
category: developer-tools
integration_type: mcp
target_agents:
  - claude-code
  - codex
supported_scopes:
  - global
  - workspace
source:
  source_type: package
  package_name: "@modelcontextprotocol/server-github"
  checksum: "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
dependencies:
  - name: npx
    type: cli
    required: true
trust:
  verification_status: verified
  publisher:
    name: Model Context Protocol Team
    url: https://github.com/modelcontextprotocol
    declared_verified: true
  allowed_executables:
    - npx
tags:
  - github
  - mcp
  - git
handler_spec:
  mcp:
    transport: stdio
    runtime: npx
    package_name: "@modelcontextprotocol/server-github"
    env_vars:
      - name: GITHUB_PERSONAL_ACCESS_TOKEN
        required: true
        secret: true
        description: GitHub PAT with repo access
```

### Key Field Descriptions
- **`id`**: Unique kebab-case identifier (e.g., `github-mcp`, `refactoring-skill`).
- **`integration_type`**: Type of integration (`mcp`, `skill`, `plugin`, `cli_tool`).
- **`target_agents`**: List of target AI agent IDs supported by this integration (`claude-code`, `codex`, or `*` for all).
- **`supported_scopes`**: Scopes allowed for installation (`global`, `workspace`).
- **`source`**: Immutable/versioned source specification (`git`, `package`, `url`, `local`).
- **`dependencies`**: Requirements (`addon`, `cli`, `agent_capability`, `runtime`).
- **`trust`**: Security status (`verified`, `community`, `unverified`), manifest publisher claims (`declared_verified`), and allowed executables whitelist.

---

## 4. Security Architecture & Execution Guardrails

### Primary Security: Typed Handler Specifications
- **`MCPHandlerSpec`**: Requires explicit `runtime` (`npx`, `uvx`, `node`, `python`) and `package_name`. Arbitrary executables (`powershell`, `cmd`, `bash`, `nc`) and arbitrary flag lists (`args`, `command`, `script`) are forbidden.
- **`SkillHandlerSpec`**: Specifies `skill_file` and `supporting_files` subject to path traversal validation.
- **`PluginHandlerSpec`**: Declares composite child `components` by ID.

### Path Traversal Protection
`validate_safe_relative_path` rejects:
- Absolute paths (`/etc/passwd`, `C:\Windows\System32`).
- Parent traversal sequences (`..`, `../`, `..\`).
- Windows drive letters (`C:\`, `D:/`) and UNC network paths (`\\server\share`).

### Source Pinning
- Git repositories must specify a 40-character hexadecimal `commit_sha`.
- Remote package and URL downloads must specify a `sha256:` checksum.

---

## 5. Trust Model & Out-of-Band Verification

- **Manifest Publisher Claims**: Manifest authors declare `publisher.name` and `publisher.declared_verified`. These are treated as informational claims.
- **Application Verification Status**: `verification_status` defaults to `UNVERIFIED`. A manifest cannot self-assert `verified` status inside its own YAML file. Verification status is assigned by the client application after validating cryptographic signatures against an official signed registry index.

---

## 6. Future Registry Synchronization Strategy

Future phases will introduce remote registry index syncing:
1. **Remote CDN Fetching**: Fetching signed `index.json` bundles over HTTPS (`httpx`).
2. **Local Caching**: Caching index data in `~/.cache/aiaddons/registry.json` with TTL validation.
3. **Custom Private Registries**: Supporting internal enterprise registries via `aiaddons registry add <url_or_path>`.
