import { expect, test, type Browser, type Page } from '@playwright/test'
import fs from 'node:fs'

const PASSWORD = 'e2e-password'
const OUT = 'e2e/out'

async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Username').fill('admin')
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible()
}

/** Pairs a fake-camera phone and returns its camera id (via the dashboard URL). */
async function pairPhone(page: Page, browser: Browser) {
  await page.getByRole('button', { name: 'Add camera' }).first().click()
  const pin = (await page.locator('.pin').innerText()).replace('-', '').trim()
  const search = new URL(await page.locator('.pin-url').innerText()).search
  const phoneContext = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 390, height: 844 },
    permissions: ['camera'],
  })
  const phone = await phoneContext.newPage()
  await phone.goto(`/pair${search}`)
  for (let i = 0; i < 6; i++) await phone.getByLabel(`Character ${i + 1}`).fill(pin[i])
  await phone.getByRole('button', { name: 'Pair' }).click()
  await expect(phone.getByText('Streaming', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Phone connected')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Close' }).click()
  return { phone, phoneContext }
}

test('draw a zone on the live frame, scripted detections produce an event', async ({
  browser,
  page,
}) => {
  const request = page.request // shares the admin cookie with the dashboard page
  fs.mkdirSync(OUT, { recursive: true })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))

  await login(page)
  await page.getByRole('button', { name: 'New session' }).first().click()
  await page.getByLabel('Name').fill('Zone session')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: 'Zone session' }).click()
  const { phone, phoneContext } = await pairPhone(page, browser)

  await page.locator('.tile .card-title').first().click()
  await expect(page.locator('.live-hero .live-overlay')).toHaveCount(0, { timeout: 20_000 })
  const cameraId = page.url().split('/cameras/')[1]

  // Draw a polygon over the lower half of the frame: click four corners, double-click to close.
  await page.getByRole('button', { name: 'Draw zone' }).click()
  const surface = page.locator('.zone-surface')
  const box = (await surface.boundingBox())!
  const at = (fx: number, fy: number) => ({ x: box.width * fx, y: box.height * fy })
  for (const [fx, fy] of [
    [0.1, 0.55],
    [0.9, 0.55],
    [0.9, 0.95],
    [0.1, 0.95],
  ] as const) {
    await surface.click({ position: at(fx, fy) })
  }
  await surface.dblclick({ position: at(0.1, 0.95) })
  await expect(page.getByText('4 / 20 points · closed')).toBeVisible()
  await page.getByLabel('Name').fill('kerb')
  await page.getByLabel('Enter after (frames)').fill('2')
  await page.getByLabel('Clear after (frames)').fill('2')
  await page.screenshot({ path: `${OUT}/m4-zone-editor.png`, fullPage: true })
  await page.getByRole('button', { name: 'Create zone' }).click()
  await expect(page.getByText('4 points · rule bottom_center')).toBeVisible()

  // Saved in normalised coordinates: reload and the zone list and overlay are identical.
  await page.reload()
  await expect(page.getByText('4 points · rule bottom_center')).toBeVisible()
  const zones = await (await request.get(`/api/cameras/${cameraId}/zones`)).json()
  expect(zones).toHaveLength(1)
  for (const [x, y] of zones[0].points) {
    expect(x).toBeGreaterThanOrEqual(0)
    expect(x).toBeLessThanOrEqual(1)
    expect(y).toBeGreaterThanOrEqual(0.5)
    expect(y).toBeLessThanOrEqual(1)
  }

  // Script the fake detector (TEST_MODE): a person whose feet are inside the zone, then nobody.
  const w = zones[0].frame_w
  const h = zones[0].frame_h
  const inside = { bbox: [w * 0.45, h * 0.4, w * 0.55, h * 0.85], conf: 0.91 }
  const script = [...Array(6).fill([inside]), ...Array(6).fill([])]
  const res = await request.post('/api/test/detector', {
    data: { script },
    headers: { 'X-Requested-With': 'publicsafe' },
  })
  expect(res.status()).toBe(204)

  await expect(page.locator('.event-row.event-zone_entered').first()).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.locator('.event-row.event-zone_cleared').first()).toBeVisible({
    timeout: 20_000,
  })
  await page.locator('.event-row').first().click()
  await expect(page.getByText('Per-rule counts for frame')).toBeVisible()
  await page.screenshot({ path: `${OUT}/m4-camera-events.png`, fullPage: true })

  // Events page shows them with filters and CSV export link.
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Events' }).click()
  await expect(page.locator('.event-row').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('link', { name: 'Export CSV' })).toHaveAttribute(
    'href',
    /\/api\/events\/export\.csv/,
  )
  const csv = await (await request.get('/api/events/export.csv')).text()
  expect(csv.split('\n')[0]).toContain('bbox_overlap,bottom_center,three_point_any')
  await page.screenshot({ path: `${OUT}/m4-events.png`, fullPage: true })

  await phone.close()
  await phoneContext.close()
  expect(errors, errors.join('\n')).toEqual([])
})
