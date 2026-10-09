// Opens the dashboard pages in Chromium, fails on console errors, saves screenshots.
// Usage: BASE_URL=https://localhost:5173 ADMIN_PASSWORD=... node e2e/screenshots.mjs <out-dir>
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const base = process.env.BASE_URL ?? 'https://localhost:5173'
const password = process.env.ADMIN_PASSWORD
const outDir = process.argv[2] ?? 'e2e/out'
if (!password) throw new Error('ADMIN_PASSWORD is required')
fs.mkdirSync(outDir, { recursive: true })

const browser = await chromium.launch()
const errors = []
const shot = async (page, name) => {
  await page.waitForTimeout(400)
  const file = path.join(outDir, `${name}.png`)
  await page.screenshot({ path: file, fullPage: true })
  console.log('saved', file)
}

async function run(scheme) {
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    colorScheme: scheme,
    viewport: { width: 1280, height: 800 },
  })
  const page = await context.newPage()
  let expectLoginFailure = true // the first sign-in uses a wrong password on purpose
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return
    if (expectLoginFailure && msg.text().includes('status of 401')) return
    errors.push(`[${scheme}] console: ${msg.text()}`)
  })
  page.on('pageerror', (err) => errors.push(`[${scheme}] pageerror: ${err.message}`))

  await page.goto(`${base}/login`)
  await page.getByLabel('Username').fill('admin')
  await page.getByLabel('Password').fill('wrong')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('alert').filter({ hasText: 'Invalid credentials' }).waitFor()
  await shot(page, `${scheme}-login-error`)

  expectLoginFailure = false
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('heading', { name: 'Sessions' }).waitFor()
  await page.waitForTimeout(600) // socket connects, banner must not show
  await shot(page, `${scheme}-sessions-empty`)

  await page.getByRole('button', { name: 'New session' }).first().click()
  await page.getByLabel('Name').fill(`Main square (${scheme})`)
  await page.getByLabel('Location note (optional)').fill('NE corner, 2 m high')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: `Main square (${scheme})` }).waitFor()
  await shot(page, `${scheme}-sessions`)

  await page.getByRole('link', { name: `Main square (${scheme})` }).click()
  await page.getByRole('heading', { name: 'Cameras' }).waitFor()
  await shot(page, `${scheme}-session-detail-empty`)

  await page.getByRole('button', { name: 'Add camera' }).first().click()
  await page.locator('.pin').waitFor()
  await page.locator('img.qr').waitFor()
  await shot(page, `${scheme}-pin-modal`)
  await page.getByRole('button', { name: 'Cancel code' }).click()

  await page.goto(`${base}/security`)
  await page.getByRole('heading', { name: 'Security' }).waitFor()
  await page.locator('table').waitFor()
  await shot(page, `${scheme}-security`)

  if (scheme === 'light') {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/`)
    await page.getByRole('heading', { name: 'Sessions' }).waitFor()
    await shot(page, 'phone-width-sessions')
  }

  const banner = await page.locator('.banner').count()
  if (banner) errors.push(`[${scheme}] stale banner visible while connected`)
  await context.close()
}

for (const scheme of ['light', 'dark']) {
  try {
    await run(scheme)
  } catch (err) {
    console.error(`[${scheme}] step failed: ${err.message.split('\n')[0]}`)
    const pages = browser.contexts().flatMap((c) => c.pages())
    if (pages.length) {
      const file = path.join(outDir, `${scheme}-FAILURE.png`)
      await pages[0].screenshot({ path: file, fullPage: true })
      console.error('failure screenshot:', file, 'url:', pages[0].url())
      const text = (await pages[0].locator('body').innerText()).slice(0, 400)
      console.error('body text:', JSON.stringify(text))
    }
    errors.push(`[${scheme}] ${err.message.split('\n')[0]}`)
    break
  }
}
await browser.close()

if (errors.length) {
  console.error('FAILED with browser errors:\n' + errors.join('\n'))
  process.exit(1)
}
console.log('OK: no console errors, no page errors')
