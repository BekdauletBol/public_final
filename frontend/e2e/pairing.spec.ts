import { expect, test, type Page } from '@playwright/test'
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

test('phone pairs with a PIN, streams with a fake camera, dashboard tile goes online, revoke stops it', async ({
  browser,
  page,
}) => {
  fs.mkdirSync(OUT, { recursive: true })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(`dashboard: ${e.message}`))

  await login(page)
  await page.getByRole('button', { name: 'New session' }).first().click()
  await page.getByLabel('Name').fill('E2E session')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: 'E2E session' }).click()
  await expect(page.getByRole('heading', { name: 'Cameras' })).toBeVisible()

  await page.getByRole('button', { name: 'Add camera' }).first().click()
  const pin = (await page.locator('.pin').innerText()).replace('-', '').trim()
  expect(pin).toHaveLength(6)
  const pairUrl = await page.locator('.pin-url').innerText()
  const search = new URL(pairUrl).search
  expect(search).toMatch(/^\?c=/)

  // The phone: separate browser context, fake camera device (config launch args).
  const phoneContext = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 390, height: 844 },
    permissions: ['camera'],
  })
  const phone = await phoneContext.newPage()
  phone.on('pageerror', (e) => errors.push(`phone: ${e.message}`))
  await phone.goto(`/pair${search}`)
  await expect(phone.getByRole('group', { name: 'Pairing code' })).toBeVisible()
  await phone.screenshot({ path: `${OUT}/m3-phone-pair.png` })
  for (let i = 0; i < 6; i++) {
    await phone.getByLabel(`Character ${i + 1}`).fill(pin[i])
  }
  await phone.getByLabel('Phone name (optional)').fill('fake phone')
  await phone.getByRole('button', { name: 'Pair' }).click()

  await expect(phone.getByText('Streaming', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(phone.getByText('This phone is streaming video to')).toBeVisible()
  await expect(phone.locator('dd', { hasText: /\d+\.\d fps/ })).toBeVisible({ timeout: 20_000 })
  await expect(phone.locator('dd', { hasText: /seq \d+/ })).toBeVisible({ timeout: 20_000 })
  await phone.screenshot({ path: `${OUT}/m3-phone-streaming.png` })

  // Dashboard: modal notices the redemption, tile turns online and shows frames.
  await expect(page.getByText('Phone connected')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Close' }).click()
  await expect(page.locator('.badge-online').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.tile .live-overlay')).toHaveCount(0, { timeout: 20_000 })
  await expect(page.locator('.metrics td', { hasText: /\d+\.\d/ }).first()).toBeVisible({
    timeout: 15_000,
  })
  await page.screenshot({ path: `${OUT}/m3-session-live.png`, fullPage: true })

  // Camera detail: hero view with frames and health numbers.
  await page.locator('.tile .card-title').first().click()
  await expect(page.getByRole('heading', { name: 'Health' })).toBeVisible()
  await expect(page.locator('.live-hero .live-overlay')).toHaveCount(0, { timeout: 20_000 })
  await page.screenshot({ path: `${OUT}/m3-camera-detail.png`, fullPage: true })

  // Health page shows the pass-through device line and no recording badge.
  await page.getByRole('link', { name: 'Health' }).click()
  await expect(page.getByRole('heading', { name: 'Health' })).toBeVisible()
  await expect(page.getByText('RECORDING')).toHaveCount(0)

  // Revoke from the camera page: the phone stops with a clear message and forgets its token.
  await page.goBack()
  await page.getByRole('button', { name: 'Revoke' }).first().click()
  await page.getByRole('dialog').getByRole('button', { name: 'Revoke' }).click()
  await expect(phone.getByText('revoked by the admin')).toBeVisible({ timeout: 10_000 })
  await phone.screenshot({ path: `${OUT}/m3-phone-revoked.png` })
  await expect(page.locator('.badge-revoked').first()).toBeVisible({ timeout: 10_000 })
  await phone.goto('/pair')
  await expect(phone.getByRole('group', { name: 'Pairing code' })).toBeVisible()

  await phoneContext.close()
  expect(errors, errors.join('\n')).toEqual([])
})

test('pair page rejects a wrong code and counts attempts', async ({ browser, page }) => {
  await login(page)
  await page.getByRole('button', { name: 'New session' }).first().click()
  await page.getByLabel('Name').fill('Wrong PIN session')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: 'Wrong PIN session' }).click()
  await page.getByRole('button', { name: 'Add camera' }).first().click()
  const search = new URL(await page.locator('.pin-url').innerText()).search

  const phoneContext = await browser.newContext({ ignoreHTTPSErrors: true })
  const phone = await phoneContext.newPage()
  await phone.goto(`/pair${search}`)
  await phone.getByLabel('Character 1').focus()
  await phone.keyboard.type('BBBBBB')
  await phone.getByRole('button', { name: 'Pair' }).click()
  await expect(phone.getByRole('alert')).toContainText('not valid')
  await expect(page.getByText('1/5 wrong attempts')).toBeVisible({ timeout: 10_000 })
  await phoneContext.close()
})
