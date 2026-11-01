import { expect, test, type Page } from '@playwright/test'
import { CHEESE, USERS, apiLogin, clearCart, open, putInCart, type Session } from './helpers'

/** Buyer (Cafe Arax) orders Lori cheese; the seller confirms and delivers; the buyer accepts the delivery. */
test.describe('order lifecycle', () => {
  let arax: Session
  let lori: Session

  test.beforeEach(async ({ request }) => {
    arax = await apiLogin(request, USERS.arax)
    lori = await apiLogin(request, USERS.lori)
    await clearCart(request, arax)
  })

  async function act(page: Page, label: string, status: string) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await expect(page.locator('header .status')).toHaveText(status)
  }

  test('buyer orders from the catalog, seller confirms and delivers, buyer accepts', async ({ page, request, context }) => {
    // Buyer: catalog → cart
    await open(page, '/', arax.token)
    await page.getByRole('searchbox', { name: 'Search' }).fill(CHEESE)
    const card = page.locator('li.product').filter({ has: page.getByRole('link', { name: CHEESE, exact: true }) })
    await expect(card.locator('.seller')).toHaveText('Լոռի Պանիր')
    await card.getByRole('button', { name: 'Add' }).click()
    // Enough cheese for the seller's minimum order
    await putInCart(request, arax, CHEESE, '12')

    await page.goto('/cart')
    await expect(page.getByRole('heading', { name: 'Cart' })).toBeVisible()
    await expect(page.getByText(CHEESE)).toBeVisible()
    await page.getByRole('button', { name: /Send all orders/ }).click()

    // Order page: a new order with an Easo number
    await expect(page).toHaveURL(/\/orders\/[0-9a-f-]+$/)
    const orderUrl = new URL(page.url()).pathname
    await expect(page.locator('header .status')).toHaveText('New')
    await expect(page.locator('.mono').first()).toHaveText(/^EASO-\d{2}-\d{6}$/)

    // Seller: in another browser profile
    const sellerPage = await context.browser()!.newPage()
    await open(sellerPage, orderUrl, lori.token)
    await expect(sellerPage.getByRole('heading', { name: 'Cafe Arax' })).toBeVisible()
    await act(sellerPage, 'Confirm', 'Confirmed')
    await act(sellerPage, 'Shipped', 'On the way')
    await act(sellerPage, 'Delivered', 'Delivered')
    await sellerPage.close()

    // Buyer accepts the delivery
    await page.reload()
    await act(page, 'Accept delivery', 'Completed')

    // The cart is empty again
    await page.goto('/cart')
    await expect(page.getByText('Your cart is empty')).toBeVisible()
  })

  test('buyer cancels a new order', async ({ page, request }) => {
    await putInCart(request, arax, CHEESE, '12')
    await open(page, '/cart', arax.token)
    await page.getByRole('button', { name: /Send all orders/ }).click()
    await expect(page.locator('header .status')).toHaveText('New')
    await act(page, 'Cancel', 'Cancelled')
  })

  test('buyer haggles, seller accepts the offer on the order page', async ({ page, request, context }) => {
    // An offer below the list price on chanakh cheese: no autopilot rule there, a person answers
    await putInCart(request, arax, 'Chanakh cheese', '8', 2900)
    await open(page, '/cart', arax.token)
    await expect(page.getByRole('button', { name: /⇄ 2 900/ })).toBeVisible()
    await page.getByRole('button', { name: /Send all orders/ }).click()
    await expect(page.locator('header .status')).toHaveText('Negotiating')
    const orderUrl = new URL(page.url()).pathname

    const sellerPage = await context.browser()!.newPage()
    await open(sellerPage, orderUrl, lori.token)
    await expect(sellerPage.getByRole('heading', { name: /Negotiation/ })).toBeVisible()
    await sellerPage.getByRole('button', { name: 'Send answer' }).click() // "accept" is preselected
    await expect(sellerPage.locator('header .status')).toHaveText('New')
    await sellerPage.close()

    await page.reload()
    await expect(page.locator('header .status')).toHaveText('New')
    await expect(page.locator('.sums')).toContainText('23 200') // 8 kg × 2 900 ֏
    await act(page, 'Cancel', 'Cancelled')
  })
})
