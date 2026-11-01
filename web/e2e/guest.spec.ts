import { expect, test } from '@playwright/test'
import { CHEESE, open } from './helpers'

test.describe('guest', () => {
  test('browses the catalog with sellers hidden and keeps a cart without signing in', async ({ page }) => {
    await open(page)
    await expect(page).toHaveTitle(/Easo/)

    await page.getByRole('searchbox', { name: 'Search' }).fill(CHEESE)
    const card = page.locator('li.product').filter({ has: page.getByRole('link', { name: CHEESE, exact: true }) })
    await expect(card).toBeVisible()
    await expect(card.getByText('Seller shown after sign-in')).toBeVisible()

    await card.getByRole('button', { name: 'Add' }).click()
    await expect(card.locator('.buy')).not.toContainText('Add')

    await page.goto('/cart')
    await expect(page.getByText(CHEESE)).toBeVisible()
    // The guest cart survives a reload: it lives in the browser
    await page.reload()
    await expect(page.getByText(CHEESE)).toBeVisible()
  })

  test('opens a product card from the catalog', async ({ page }) => {
    await open(page)
    await page.getByRole('searchbox', { name: 'Search' }).fill(CHEESE)
    await page.locator('a.product-link', { hasText: CHEESE }).first().click()
    await expect(page).toHaveURL(/\/product\//)
    await expect(page.getByRole('heading', { name: CHEESE })).toBeVisible()
  })

  test('cannot reach orders without signing in', async ({ page }) => {
    await open(page, '/orders')
    await expect(page).toHaveURL(/\/$/)
  })
})
