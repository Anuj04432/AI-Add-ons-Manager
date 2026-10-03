# Contributing to aiaddons

Thank you for your interest in contributing to `aiaddons`! We welcome contributions to both the core manager codebase and the add-on registry.

## Development environment setup

`aiaddons` requires Python 3.11 or higher.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Anuj04432/AI-Add-ons-Manager.git
   cd AI-Add-ons-Manager
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv

   # On Linux/macOS:
   source .venv/bin/activate

   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   ```

3. **Install the package in editable mode with development dependencies**:
   ```bash
   pip install -e ".[dev]"
   ```

## Running the test suite & code quality tools

Before opening a pull request, ensure all tests, type checks, and linters pass:

- **Run unit and integration tests**:
  ```bash
  pytest
  ```

- **Run strict static type checking**:
  ```bash
  mypy src
  ```

- **Run the linter and code formatter checks**:
  ```bash
  ruff check src tests
  ruff format --check src tests
  ```

## Registry manifest submissions

`aiaddons` prioritizes reliability and security over sheer registry volume. A registry entry is not acceptable merely because it passes schema validation (`pydantic` parsing).

Given the project's history of catching fabricated package versions and invalid Git commit hashes, **every new registry manifest must be real-world verified**:

- **Real commit SHAs**: For Git-sourced add-ons, the `commit_sha` must point to an actual reachable commit in the upstream repository, and any relative path specified must exist inside that tree.
- **Published packages**: For package-sourced add-ons (such as npm packages), the package must resolve on the live registry and the declared `version` must exist in published versions.
- **Automated verification**: All registry additions are tested against `tests/integration/test_registry_validation.py`. Run this suite to verify your manifest:
  ```bash
  pytest tests/integration/test_registry_validation.py
  ```
- **Manifest rules**: Manifests must conform strictly to schema specifications in [`docs/REGISTRY.md`](docs/REGISTRY.md) with `extra = "forbid"`. Manifests must never attempt to invoke arbitrary shell scripts or unverified executables.

## Commit message convention

We follow the Conventional Commits specification. Commit messages should begin with a standard prefix describing the change:

- `feat`: A new feature or capability (e.g. `feat(agents): add adapter for new agent`)
- `fix`: A bug fix (e.g. `fix(execution): handle timeout during rollback`)
- `test`: Adding or updating test cases (e.g. `test(registry): add real verification tests`)
- `refactor`: Code reorganization without functional changes (e.g. `refactor(core): decouple resolver logic`)
- `chore`: Maintenance tasks, dependency updates, or configuration changes
- `docs`: Documentation updates (e.g. `docs: update README with branding and usage`)
