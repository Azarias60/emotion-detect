import { test, expect } from 'playwright/test'
import { backend, report, rows, signIn } from './fixtures.mjs'

test('首页也依赖登录身份，登录页独立于工作区', async ({ page }) => {
  await backend(page)
  await page.goto('/')
  await expect(page).toHaveURL(/\/login(?:\?|$)/)
  await expect(page.getByLabel(/^(用户名|账号)$/)).toBeVisible()
  await expect(page.getByRole('link', { name: '报告管理', exact: true })).toHaveCount(0)
})

test('查询按钮应用草稿条件，匹配使用逐题原文而非显示摘要', async ({ page }) => {
  await backend(page, [
    report(1, 'health', '健康解读', [{ question: '睡眠？', answer: '最近早醒' }]),
    report(2, 'emotion', '情绪平稳'), report(3, 'fatigue', '注意休息'),
  ])
  await signIn(page); await page.goto('/reports')
  await expect(rows(page)).toHaveCount(3)
  await page.getByLabel(/^关键[词字]$/).fill('早醒')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page)).toContainText(/健康/)
})

test('报告管理通过弹窗查看完整逐题问答', async ({ page }) => {
  const answer = '完整回答'.repeat(100)
  await backend(page, [report(1, 'health', '健康解读', [{ question: '睡眠如何？', answer }])])
  await signIn(page); await page.goto('/reports')
  await rows(page).getByRole('button', { name: /问答/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('睡眠如何？')
  await expect(dialog).toContainText(answer)
})

test('综合报告直接汇总完整匹配集合，关闭后不产生历史条目或保存请求', async ({ page }) => {
  const state = await backend(page, Array.from({ length: 12 }, (_, index) => report(index + 1, 'health', `报告正文${index + 1}`)))
  await signIn(page); await page.goto('/reports')
  await expect(page.getByRole('table').first()).toBeVisible()
  await page.getByRole('button', { name: /综合报告/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('12')
  await expect(dialog).toContainText('报告正文12')
  await expect(dialog.getByRole('button', { name: /重新分析|保存/ })).toHaveCount(0)
  await dialog.getByRole('button', { name: /close|关闭/i }).first().click()
  expect(state.reports).toHaveLength(12)
  expect(state.calls.filter(call => call.method === 'POST' && /\/reports(?:\/|$)/.test(call.path))).toEqual([])
})

test('删除更新原集合，已应用的查询条件继续生效', async ({ page }) => {
  await backend(page, [report(1, 'health', '匹配保留记录'), report(2, 'health', '匹配待删除记录'), report(3, 'emotion', '其他记录')])
  await signIn(page); await page.goto('/reports')
  await expect(rows(page)).toHaveCount(3)
  await page.getByLabel(/^关键[词字]$/).fill('匹配')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await expect(rows(page)).toHaveCount(2)
  const target = rows(page).filter({ hasText: '匹配待删除记录' })
  await target.getByRole('button', { name: /删除/ }).click()
  await page.getByRole('button', { name: /确定|确认/ }).last().click()
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page)).toContainText('保留记录')
})

test('无命令的事实变化经观察流到达界面，全程无报告命令', async ({ page }) => {
  const state = await backend(page)
  await signIn(page); await page.goto('/reports')
  await expect(rows(page)).toHaveCount(0)
  // 后台分析完成：事实所有者发布新报告，不经过任何页面命令。
  state.reports.push(report(7, 'fatigue', '后台分析完成的新报告'))
  state.pushReports()
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page)).toContainText('后台分析完成的新报告')
  const reportCommands = state.calls.filter(
    call => call.method !== 'GET' && /\/reports(?:\/|$)/.test(call.path))
  expect(reportCommands).toEqual([])
})

test('首页入口、检测模式菜单和数字人操作均可通过页面切换', async ({ page }) => {
  await backend(page); await signIn(page)
  await page.getByRole('textbox').first().click()
  await page.getByRole('button', { name: '开始聆听' }).click()
  await page.getByRole('button', { name: '停止聆听' }).click()
  await page.getByRole('button', { name: '重新开始' }).click()
  await page.getByRole('link', { name: '健康检测', exact: true }).click()
  await expect(page).toHaveURL(/\/detection\?mode=health$/)
  const navigation = page.getByRole('navigation', { name: '主导航' })
  const modes = navigation.getByRole('button', { name: '检测中心' })
  await modes.click()
  await expect(page.locator('.ant-dropdown').filter({ has: page.getByRole('menu') })).toHaveCSS('opacity', '1')
  await page.getByRole('menuitem', { name: '疲劳检测' }).click()
  await expect(page).toHaveURL(/\/detection\?mode=fatigue$/)
  await modes.focus()
  await page.keyboard.press('Enter')
  await page.getByRole('menuitem', { name: '情绪检测' }).press('Enter')
  await expect(page).toHaveURL(/\/detection\?mode=emotion$/)
  await page.goBack()
  await expect(page).toHaveURL(/\/detection\?mode=fatigue$/)
  await navigation.getByRole('link', { name: '数字人', exact: true }).click()
  await expect(page).toHaveURL(/\/digital-human$/)
  await expect(page.getByRole('textbox').first()).toBeVisible()
  for (const name of ['制作数字人', '音色克隆与绑定', '模型 Key 设置']) {
    await page.getByRole('button', { name, exact: true }).click()
    const dialog = page.getByRole('dialog', { name })
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  }
  await page.getByRole('button', { name: '管理', exact: true }).click()
  await page.getByRole('tab', { name: '音色', exact: true }).click()
  await expect(page.getByRole('tab', { name: '音色', exact: true })).toHaveAttribute('aria-selected', 'true')
})
