import { test, expect } from 'playwright/test'
import { backend, report, signIn } from './fixtures.mjs'

test('桌面各页共用一个 Scene，规范控件可达且页面不横向溢出', async ({ page }, info) => {
  await backend(page, [report(1, 'health', '本轮解读')])
  await page.goto('/login')
  const surfaces = page.locator('[data-liquid-glass]:visible')
  await expect(surfaces).toHaveCount(1)
  await expect(page.locator('#workspace')).toBeVisible()
  await page.screenshot({ path: info.outputPath('login.png') })
  await signIn(page)
  for (const [route, name] of [['/', 'home'], ['/detection', 'detection'], ['/reports', 'reports'], ['/digital-human', 'human'], ['/account', 'account']]) {
    await page.goto(route)
    await expect(page.locator('[data-liquid-glass-scene]:not(canvas)')).toHaveCount(1)
    await expect(surfaces).toHaveCount(3)
    for (const id of ['workspace', 'avatar']) {
      const surface = page.locator('#' + id)
      await expect(surface).toBeVisible()
      await expect(surface).toHaveAttribute('data-glass-strategy', /^(svg|webgl|blur)$/)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath(name + '.png') })
  }
  const avatar = page.getByRole('link', { name: '当前账号' })
  await page.getByRole('navigation').getByRole('link', { name: '首页' }).click()
  await avatar.focus()
  await expect(avatar).toBeFocused()
  await avatar.press('Enter')
  await expect(page.getByRole('heading', { name: '登录会话' })).toBeVisible()
  await page.getByRole('navigation').getByRole('link', { name: '首页' }).click()
  await avatar.click()
  await expect(page.getByRole('heading', { name: '登录会话' })).toBeVisible()
  await page.goto('/reports')
  await expect(page.getByRole('button', { name: '查询', exact: true })).toBeVisible()
  await page.getByRole('button', { name: /综合报告/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('[data-liquid-glass-scene]:not(canvas)')).toHaveCount(1)
  await page.screenshot({ path: info.outputPath('report-modal.png') })
})
