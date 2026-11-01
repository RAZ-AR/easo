import { expect, test, type Page } from '@playwright/test'
import { USERS, apiLogin, open } from './helpers'

/** Signed in as a buyer: the catalog with the buyer's tabs. */
async function expectBuyerHome(page: Page) {
  await expect(page.getByRole('heading', { name: /^Catalog/ })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Orders' }).first()).toBeVisible()
}

test.describe('sign-in', () => {
  test.beforeEach(async ({ request }) => {
    await apiLogin(request, USERS.arax) // pending consents accepted, so no gate in the way
  })

  test('a demo buyer signs in from the login page', async ({ page }) => {
    await open(page, '/login')
    await page.getByRole('button', { name: /Cafe Arax — owner/ }).click()
    await expectBuyerHome(page)
  })

  // One code per run: the API allows 20 codes an hour from one address
  test('e-mail sign-in refuses a wrong code, then accepts the right one', async ({ page }) => {
    await open(page, '/login')
    await page.getByLabel('E-mail').fill(USERS.arax)
    await page.getByRole('button', { name: /Get a code/ }).click()
    const code = (await page.locator('.notice b.num').textContent())?.trim() ?? ''
    expect(code).toMatch(/^\d{6}$/)

    const wrong = code === '000000' ? '111111' : '000000'
    await page.getByLabel('Code from the e-mail').fill(wrong)
    await page.getByRole('button', { name: /^Sign in/ }).click()
    await expect(page.locator('.error')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)

    await page.getByLabel('Code from the e-mail').fill(code)
    await page.getByRole('button', { name: /^Sign in/ }).click()
    await expectBuyerHome(page)
  })

  test('a sign-in saved under the old QB keys carries over', async ({ page, request }) => {
    const { token } = await apiLogin(request, USERS.arax)
    await page.addInitScript((t) => {
      if (sessionStorage.getItem('e2e.legacy')) return
      sessionStorage.setItem('e2e.legacy', '1')
      localStorage.clear()
      localStorage.setItem('qb.token', t)
      localStorage.setItem('qb.lang', 'en')
    }, token)
    await page.goto('/')
    await expectBuyerHome(page)
    expect(await page.evaluate(() => [localStorage.getItem('easo.token') !== null, localStorage.getItem('qb.token')])).toEqual([
      true,
      null,
    ])
  })
})
