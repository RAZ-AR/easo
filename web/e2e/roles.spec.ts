import { expect, test } from '@playwright/test'
import { USERS, apiLogin, open } from './helpers'

test('seller lands on the inbox and sees its products', async ({ page, request }) => {
  const { token } = await apiLogin(request, USERS.lori)
  await open(page, '/', token)
  await expect(page.getByRole('link', { name: 'Inbox' }).first()).toBeVisible()
  await page.getByRole('link', { name: 'Products' }).first().click()
  await expect(page).toHaveURL(/\/seller\/products/)
  await expect(page.getByRole('main').getByText('Lori cheese', { exact: true }).first()).toBeVisible()
})

test('the operator lands on moderation', async ({ page, request }) => {
  const { token } = await apiLogin(request, USERS.admin)
  await open(page, '/', token)
  await expect(page.getByRole('link', { name: 'Moderation' }).first()).toBeVisible()
})
