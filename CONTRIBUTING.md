# Contributing to GTA AI ECI Workers

Thank you for considering contributing to this project! Please follow these guidelines to ensure a smooth collaboration.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Branch Naming Convention](#branch-naming-convention)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Issue Reporting](#issue-reporting)

## Code of Conduct

Be respectful, inclusive, and professional in all interactions. Harassment or discriminatory behavior will not be tolerated.

## Getting Started

1. Fork the repository.
2. Clone your fork locally.
3. Create a new branch from `main` following the [branch naming convention](#branch-naming-convention).
4. Make your changes.
5. Push your branch and open a Pull Request.

## Branch Naming Convention

Use the following format:

```
<type>/<short-description>
```

**Types:**
- `feature/{ISSUE-ID}-` — New features
- `task/{ISSUE-ID}-` — Simple Sub task
- `bugfix/{ISSUE-ID}-` — Bug fixes
- `hotfix/{ISSUE-ID}-` — Urgent production fixes
- `chore/{ISSUE-ID}-` — Maintenance tasks (CI, docs, deps)
- `refactor/{ISSUE-ID}-` — Code refactoring

**Examples:**
- `feature/{ISSUE-ID}-add-face-verification-logic`
- `bugfix/{ISSUE-ID}-fix-encoding-timeout`

## Commit Message Guidelines

Follow the [Conventional Commits](https://www.conventionalcommits.org/) standard:

```
<type>(<scope>): <short summary>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`

**Examples:**
- `feat(face_encoding_worker): add retry logic to pubsub subscriber`
- `fix(common): resolve GCP authentication timeout`
- `docs: update contributing guidelines`

## Pull Request Process

1. Ensure your branch is up to date with `main`.
2. Fill out the PR template completely.
3. Link related issues using `Closes #<issue-number>`.
4. Ensure all tests pass before requesting review.
5. Request at least **one** reviewer.
6. Do not merge your own PR — wait for approval.

## Coding Standards

- **Language:** Python 3.11+
- **Style:** Follow [PEP 8](https://peps.python.org/pep-0008/).
- **Imports:** Use absolute imports. Group into standard library, third-party, and local.
- **Type Hints:** Use type annotations for all function signatures.
- **Docstrings:** Use Google-style docstrings for all public modules, classes, and functions.
- **Environment Variables:** Never commit secrets. Use `.env.example` files as references.

## Testing

The test suite lives under the top-level `tests/` directory, organized by type:

```
tests/
├── unit/           # Fast, isolated tests (all dependencies mocked) — no Docker
│   ├── common/     # Shared services & utils (storage, encoders, helpers)
│   └── workers/    # Per-worker handlers, schemas, services, strategies
├── integration/    # Tests against Pub/Sub + GCS emulators (marked `integration`)
└── load/           # Load-testing framework (see docs/testing/load/)
```

- Write **unit tests** for all new functionality and place them under
  `tests/unit/` (mirroring the `common/` or `workers/<worker_name>/` layout).
- Add **integration tests** under `tests/integration/` and mark them with
  `@pytest.mark.integration`.
- Run tests locally before pushing:

```bash
# Unit tests + coverage (no emulators needed)
python -m pytest tests/unit/

# A single module or worker
python -m pytest tests/unit/workers/face_encoding_worker/

# Integration tests (requires Docker emulators)
docker compose -f tests/integration/docker-compose.yml up -d
python -m pytest tests/integration/ -m integration
docker compose -f tests/integration/docker-compose.yml down -v
```

- Aim for meaningful coverage — focus on critical paths and edge cases.
  The unit-test workflow enforces a minimum coverage threshold (`--cov-fail-under`).

## Issue Reporting

- Use the provided [issue templates](/.github/ISSUE_TEMPLATE/) when creating issues.
- Provide as much detail as possible (steps to reproduce, environment, logs).

---

Thank you for contributing! 🎉
