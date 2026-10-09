// Starts a TEST_MODE backend on https://localhost:8445 serving frontend/dist for Playwright.
// Password: e2e-password. Database: a temp file. Fake detector so tests can script detections.
import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '../..')
const python = path.join(root, '.venv/bin/python')
const port = process.env.E2E_PORT ?? '8445'
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'publicsafe-e2e-'))

const hash = execFileSync(python, [
  '-c',
  "from argon2 import PasswordHasher as P; print(P(time_cost=1, memory_cost=8192, parallelism=1).hash('e2e-password'))",
])
  .toString()
  .trim()

const env = {
  ...process.env,
  ADMIN_USERNAME: 'admin',
  ADMIN_PASSWORD_HASH: hash,
  DATABASE_URL: `sqlite+pysqlite:///${path.join(work, 'e2e.db')}`,
  TEST_MODE: '1',
  DETECTOR: 'fake',
  LOG_LEVEL: 'WARNING',
  OFFLINE_AFTER_S: '10',
  METRIC_SAMPLE_INTERVAL_S: '2',
}

const child = spawn(
  python,
  ['-m', 'publicsafe.cli', 'run', '--host', '127.0.0.1', '--port', port],
  { cwd: path.join(root, 'backend'), env, stdio: 'inherit' },
)
const stop = () => {
  child.kill('SIGTERM')
  fs.rmSync(work, { recursive: true, force: true })
}
process.on('SIGTERM', () => {
  stop()
  process.exit(0)
})
process.on('SIGINT', () => {
  stop()
  process.exit(0)
})
child.on('exit', (code) => {
  fs.rmSync(work, { recursive: true, force: true })
  process.exit(code ?? 1)
})
