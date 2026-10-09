# ADR 0001: Python 3.12 via uv, fully pinned freeze, headless OpenCV only

Status: Accepted (M0, 2026-10-10)

## Context

The spec asks for the latest Python 3.12 patch, a fully pinned `requirements.txt`
produced by `pip freeze`, and `opencv-python-headless` (the `core` package may
only decode). The Mac had Python 3.12.0, 3.13.13 and 3.14.5 installed, none
being "latest 3.12". `ultralytics` 8.4.174 declares a dependency on the GUI
build `opencv-python`, which would install a second `cv2` (5.0.0.93 at the time)
next to the headless 4.14.0.94 build.

## Decision

- Python 3.12.13 is installed and managed by `uv` (`.python-version`); `make setup`
  creates `.venv` from it. A manual `python3.12 -m venv` path is documented.
- `backend/pyproject.toml` pins every direct dependency with `==`.
  `make lock` resolves it with the override file `backend/overrides.txt`, which
  removes ultralytics' `opencv-python` requirement, and freezes the result into
  `backend/requirements.txt`. `make setup` installs that file with `--no-deps`,
  so installs are exact and never re-add the GUI OpenCV.
- Node 24.21.0 via `.nvmrc`. TypeScript is pinned to the newest 6.0.x because
  `typescript-eslint` 8.71.1 rejects TypeScript 7 (peer range `<6.1.0`).

## Alternatives

- Python 3.13 (installed, wheels exist): rejected to follow the spec's verified set.
- `pip install ultralytics --no-deps` and listing its dependencies by hand: fragile across upgrades.
- Accepting both OpenCV builds: import order would decide which `cv2` loads.

## Consequences

- Anyone changing a dependency edits `pyproject.toml` and runs `make lock`, then commits the freeze.
- `pip check` reports ultralytics' missing `opencv-python` requirement; this is expected and documented.
- Upgrading to TypeScript 7 waits for typescript-eslint support.
