import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = fileURLToPath(new URL('../src/', import.meta.url))
const source = relative => fs.readFileSync(path.join(root, relative), 'utf8')
function files(directory = root) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const name = path.join(directory, entry.name)
    return entry.isDirectory() ? files(name) : /\.[jt]sx?$/.test(name) ? [name] : []
  })
}
const parsed = () => files().map(filename => ({ filename,
  tree: ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true),
}))

test('设备契约由设备职责提供，预览消费者不依赖检测业务域', () => {
  const violations = []
  for (const { filename, tree } of parsed()) {
    if (!/preview/i.test(path.basename(filename))) continue
    for (const statement of tree.statements) {
      if (ts.isImportDeclaration(statement) && /domain\/detection$/.test(statement.moduleSpecifier.text))
        violations.push(path.relative(root, filename))
    }
  }
  assert.deepEqual(violations, [], '设备预览不应通过检测域获取通用设备契约')
})

test('前端检测消费面不再拥有生成后保存副本的写入契约', () => {
  const violations = []
  for (const relative of ['domain/detection.ts', 'domain/reports.ts']) {
    const tree = ts.createSourceFile(relative, source(relative), ts.ScriptTarget.Latest, true)
    for (const statement of tree.statements) {
      if (ts.isInterfaceDeclaration(statement) && ['DetectionReportSaver', 'SaveReport'].includes(statement.name.text))
        violations.push(`${relative}: ${statement.name.text}`)
    }
  }
  assert.deepEqual(violations, [], '报告由后端能力记录，前端不拼装和补存检测结果')
})

test('Ant Design 主题与玻璃各自只有一个配置所有者', () => {
  const owners = { theme: new Set(), glass: new Set() }
  for (const { filename, tree } of parsed()) for (const statement of tree.statements) {
    if (!ts.isImportDeclaration(statement)) continue
    const spec = statement.moduleSpecifier.text
    const bindings = statement.importClause?.namedBindings
    if (!bindings || !ts.isNamedImports(bindings)) continue
    for (const binding of bindings.elements) {
      const name = (binding.propertyName ?? binding.name).text
      if ((spec === 'antd' && name === 'ConfigProvider') ||
          (spec === '@ant-design/x' && name === 'XProvider')) owners.theme.add(filename)
      if (spec === 'simple-liquid-glass/backdrop' && name === 'LiquidGlassScene') owners.glass.add(filename)
    }
  }
  assert.equal(owners.theme.size, 1, '统一主题需有且只有一个配置所有者')
  assert.equal(owners.glass.size, 1, '全站只使用一条库提供的 Scene 通路')
})

test('React 接入不通过定时 tick 驱动检测业务，前端不启动后台计算或保存副本', () => {
  const violations = []
  for (const { filename, tree } of parsed()) {
    function visit(node) {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
        const method = node.expression.name.text
        if (['saveDetection', 'prepareHealth'].includes(method)
            || (method === 'tick' && /detection/i.test(path.relative(root, filename))))
          violations.push(`${path.relative(root, filename)}: ${method}`)
      }
      ts.forEachChild(node, visit)
    }
    visit(tree)
  }
  assert.deepEqual(violations, [], '前端消费后端事实，准备、业务计时和最终记录由后端承担')
})
