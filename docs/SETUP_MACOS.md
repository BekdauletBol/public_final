# Setting up public.safe on macOS

Target: Apple Silicon Mac (arm64), macOS 14 or newer (required for PyTorch MPS).
Nothing here needs a GitHub account.

## 1. Prerequisites

| Tool | Version used | Install |
|---|---|---|
| Homebrew | any recent | https://brew.sh |
| uv | 0.12+ | `brew install uv` (manages the Python build) |
| Python | 3.12.13 (from `.python-version`) | installed by `uv` on first `make setup` |
| Node.js | 24.21.0 (from `.nvmrc`) | `brew install nvm` then `nvm install` in the repo, or any Node 24 on PATH |
| mkcert | 1.4.4 | `brew install mkcert` |
| Docker Desktop | optional | only for the PostgreSQL smoke test (`docker compose up -d db`, port 55432) |

Without `uv`: create the venv manually and install the same pinned file:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --no-deps -r backend/requirements.txt
.venv/bin/pip install --no-deps -e backend
```

## 2. One-time setup

```bash
make setup            # .venv + pinned Python deps, npm ci, copies .env.example to .env
mkcert -install       # once per Mac: creates the dev root CA and trusts it (asks for your password)
make certs            # certs/dev.pem + certs/dev-key.pem for localhost, your LAN IPs, <host>.local
```

`make certs` prints the steps for trusting the root CA on phones (also in
[PHONE_SETUP.md](PHONE_SETUP.md)). Re-run `make certs` with
`.venv/bin/publicsafe certs --force` whenever the Mac's LAN IP changes.

Set the admin password once (prompts twice, writes the argon2id hash to `.env`,
logs out every admin session):

```bash
.venv/bin/publicsafe admin set-password
```

Non-interactive: `printf 'your-password\n' | .venv/bin/publicsafe admin set-password --password-stdin`.

## 3. Running

| Command | What it does | Phone URL |
|---|---|---|
| `make dev` | backend on `https://0.0.0.0:8443` with reload, Vite on `https://0.0.0.0:5173` proxying `/api` and `/ws` | `https://<lan-ip>:5173/pair` |
| `make run` | builds the frontend and serves everything from 8443 (demo setup) | `https://<lan-ip>:8443/pair` |

Health check: `curl https://localhost:8443/api/health` (the Mac trusts the mkcert CA after `mkcert -install`).

## 4. Tests and quality

```bash
make test          # unit + integration + security + cv, no network/GPU
make lint          # ruff, black, mypy --strict (core), eslint, prettier, tsc
make format
make test-model    # real YOLOv8n (downloads weights)
make test-browser  # Playwright + Chromium with a fake camera against a TEST_MODE backend on :8445
make test-load     # 3 fake phones at 15 fps, latest then fifo (LOADTEST_DURATION=60 by default)
```

## 5. Troubleshooting

- **"Refusing to start: ADMIN_PASSWORD_HASH is not set"**: run `.venv/bin/publicsafe admin set-password`.
- **"Database schema is at revision ..."**: `AUTO_MIGRATE=false` and the schema is behind; run `cd backend && ../.venv/bin/alembic upgrade head`.
- **PostgreSQL round-trip test**: `docker compose up -d db`, then
  `PUBLICSAFE_TEST_PG_URL=postgresql+psycopg://publicsafe:publicsafe@127.0.0.1:55432/publicsafe make test`.
- **"SSL_CERTFILE not found"**: run `make certs`.
- **Browser shows a certificate warning on the Mac**: run `mkcert -install`, then restart the browser.
- **Phone shows a certificate warning**: install the root CA on the phone (PHONE_SETUP.md) and make sure the certificate was issued for the IP you are using (`make certs` output lists the names).
- **`uv` not found**: `brew install uv`, or use the manual venv instructions above.
- **"Could not load the YOLOv8n detector"**: the first start downloads `yolov8n.pt` into `data/models/`; check network access, or set `DETECTOR=none` to run without detection.
- **"MPS unavailable"** (Intel Mac or old macOS): inference falls back to CPU automatically; lower the cameras' target fps (5) and max long edge (960) in the camera settings.
