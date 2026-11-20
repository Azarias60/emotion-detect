import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const srcRoot = fileURLToPath(new URL('../src/', import.meta.url))
// 反向测试夹具：故意越界的导入形态，必须被同一套门禁逐条拒绝。
const fixtureRoot = fileURLToPath(new URL('./fixtures/boundary-violations/', import.meta.url))
const validUiRoot = fileURLToPath(new URL('./fixtures/ui-contract-valid/', import.meta.url))

const allowed = new Map(Object.entries({
  // 组合根拥有页面装配权，布局 Shell 不拥有页面分发权。
  root: ['root', 'components', 'app', 'bootstrap', 'domain', 'pages'],
  domain: ['domain'],
  application: ['application', 'domain'],
  api: ['api', 'domain'],
  bootstrap: ['bootstrap', 'api', 'application', 'domain'],
  app: ['app', 'pages', 'components', 'scenes', 'domain', 'application'],
  pages: ['pages', 'components', 'scenes', 'domain'],
  components: ['components', 'scenes', 'domain'],
  scenes: ['scenes', 'assets'],
}))
const uiExternal = new Set(['react', 'react-dom', 'react-dom/client', 'lucide-react'])
// Audited Markdown stack; its sole owner is the single file below, not the whole components/ layer.
const renderingLibraries = new Set(['react-markdown', 'remark-gfm', 'remark-parse', 'unified', 'mdast-util-to-string', 'mdast'])
const markdownOwner = 'components/MarkdownContent.tsx'
// 全站玻璃光学库；唯一所有者是 GlassPanel，业务视图不得绕过它直连库。
const glassLibraries = new Set(['simple-liquid-glass', 'simple-liquid-glass/backdrop'])
const glassOwner = 'components/GlassPanel.tsx'
// Planned UI composition point, not a requirement to install/create it in this task.
const uiProviderOwner = 'app/UiProvider.tsx'
const controlLayers = new Set(['root', 'app', 'pages', 'components'])
const controlLibraries = new Set(['antd', '@ant-design/icons', '@ant-design/x', 'dayjs'])
const providerResources = new Set(['antd/locale/zh_CN', '@ant-design/x/locale/zh_CN', 'antd/dist/reset.css'])
// Graphics infrastructure owns native runtime/image loading; business views still cannot do I/O.
const viewLayers = new Set(['root', 'app', 'pages', 'components', 'scenes'])
const coreLayers = new Set(['domain', 'application'])
const prohibitedIO = new Set([
  'fetch', 'XMLHttpRequest', 'RTCPeerConnection',
  'localStorage', 'sessionStorage', 'getUserMedia',
])
const prohibitedCore = new Set([
  ...prohibitedIO, 'window', 'document', 'navigator',
  'MediaStream', 'setTimeout', 'setInterval',
])
const staticExtensions = new Set(['.css', '.json', '.png', '.jpg', '.jpeg', '.webp', '.svg'])

function analyze(root) {
  const files = []
  const diagnostics = []

  function collect(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name)
      if (entry.isDirectory()) collect(full)
      else if (entry.name.endsWith('.vue'))
        diagnostics.push(label(full) + ': Vue 仅作设计参考，禁止编译 Vue 组件')
      else if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) files.push(full)
    }
  }
  function layer(filename) {
    const segments = path.relative(root, filename).split(path.sep)
    return segments.length === 1 ? 'root' : segments[0]
  }
  function label(filename) {
    return path.relative(root, filename).split(path.sep).join('/')
  }
  collect(root)

  for (const filename of files) {
    const source = fs.readFileSync(filename, 'utf8')
    const syntax = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true,
      filename.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    const from = layer(filename)
    const relative = label(filename)
    const appBindings = new Set()
    for (const statement of syntax.statements) {
      if (!ts.isImportDeclaration(statement) || statement.moduleSpecifier.text !== 'antd') continue
      const bindings = statement.importClause?.namedBindings
      if (bindings && ts.isNamedImports(bindings))
        for (const binding of bindings.elements)
          if ((binding.propertyName ?? binding.name).text === 'App') appBindings.add(binding.name.text)
    }
    if (!allowed.has(from)) diagnostics.push(relative + ': 未声明的架构目录 ' + from)
    for (const item of syntax.parseDiagnostics)
      diagnostics.push(relative + ': TypeScript 语法错误 ' + ts.flattenDiagnosticMessageText(item.messageText, ' '))

    function checkSpecifier(node) {
      if (!ts.isStringLiteral(node)) return
      const spec = node.text
      if (!spec.startsWith('.')) {
        if (['vue', 'vue-router', 'element-plus', 'pinia'].some(pkg => spec === pkg || spec.startsWith(pkg + '/')))
          diagnostics.push(relative + ': Vue 仅作设计参考，禁止运行时依赖 ' + spec)
        else if (coreLayers.has(from))
          diagnostics.push(relative + ': 核心用例不允许外部运行时依赖 ' + spec)
        else if (renderingLibraries.has(spec)) {
          if (relative !== markdownOwner)
            diagnostics.push(relative + ': Markdown 渲染库仅允许由 ' + markdownOwner + ' 导入 (' + spec + ')')
        } else if (spec === 'simple-liquid-glass' || spec.startsWith('simple-liquid-glass/')) {
          if (relative !== glassOwner)
            diagnostics.push(relative + ': 玻璃库仅允许由 ' + glassOwner + ' 导入 (' + spec + ')')
          else if (!glassLibraries.has(spec))
            diagnostics.push(relative + ': 未审计的玻璃库入口 ' + spec)
        } else if (providerResources.has(spec)) {
          if (relative !== uiProviderOwner)
            diagnostics.push(relative + ': Ant Design 全局资源仅允许由 ' + uiProviderOwner + ' 导入 (' + spec + ')')
        } else if (spec.startsWith('antd/')) {
          diagnostics.push(relative + ': 禁止未审计的 Ant Design 子路径 ' + spec)
        } else if (controlLibraries.has(spec)) {
          if (!controlLayers.has(from))
            diagnostics.push(relative + ': 基础设施不得依赖 UI 库 ' + spec)
          if (spec === 'antd') {
            const declaration = node.parent
            if (ts.isImportDeclaration(declaration)) {
              const clause = declaration.importClause
              const bindings = clause?.namedBindings
              if (!bindings || !ts.isNamedImports(bindings) || clause.name)
                diagnostics.push(relative + ': Ant Design 必须使用具名导入以检查全局配置所有权')
              else for (const binding of bindings.elements) {
                const name = (binding.propertyName ?? binding.name).text
                if (name === 'ConfigProvider' && relative !== uiProviderOwner)
                  diagnostics.push(relative + ': Ant Design 全局配置仅允许由 ' + uiProviderOwner + ' 导入 (' + name + ')')
              }
            } else if (ts.isExportDeclaration(declaration)) {
              const bindings = declaration.exportClause
              if (!bindings || !ts.isNamedExports(bindings))
                diagnostics.push(relative + ': 禁止通配转发 Ant Design 全局配置能力')
              else for (const binding of bindings.elements) {
                const name = (binding.propertyName ?? binding.name).text
                if (['ConfigProvider', 'App'].includes(name))
                  diagnostics.push(relative + ': 禁止转发 Ant Design 全局配置能力 ' + name)
              }
            }
          }
        } else if (viewLayers.has(from) && !uiExternal.has(spec))
          diagnostics.push(relative + ': 视图模块未审计的外部依赖 ' + spec)
        else if (['api', 'bootstrap'].includes(from) && uiExternal.has(spec))
          diagnostics.push(relative + ': 基础设施不得依赖 UI 库 ' + spec)
        return
      }
      const destination = path.resolve(path.dirname(filename), spec)
      const extension = path.extname(spec).toLowerCase()
      if (staticExtensions.has(extension)) {
        if (coreLayers.has(from) || from === 'api' || from === 'bootstrap')
          diagnostics.push(relative + ': 业务/基础设施层禁止引入视图静态资源 ' + spec)
        return
      }
      const destRelative = path.relative(root, destination)
      if (destRelative === '..' || destRelative.startsWith('..' + path.sep))
        diagnostics.push(relative + ': 禁止跨 src 根目录导入代码 ' + spec)
      else if (!allowed.get(from)?.includes(layer(destination)))
        diagnostics.push(relative + ': 非法依赖方向 ' + from + ' → ' + layer(destination) + ' (' + spec + ')')
    }

    function visit(node) {
      if (ts.isImportDeclaration(node)) checkSpecifier(node.moduleSpecifier)
      if (ts.isExportDeclaration(node) && node.moduleSpecifier) checkSpecifier(node.moduleSpecifier)
      if (ts.isImportEqualsDeclaration(node))
        diagnostics.push(relative + ': 禁止 import = require 别名绕过边界')
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword)
        diagnostics.push(relative + ': 禁止动态 import 绕过边界')
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'require')
        diagnostics.push(relative + ': 禁止 require 绕过边界')
      if (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression)) {
        const name = node.argumentExpression.text
        if (name === 'getUserMedia'
            || (coreLayers.has(from) && prohibitedCore.has(name))
            || (viewLayers.has(from) && prohibitedIO.has(name)))
          diagnostics.push(relative + ': 禁止计算属性绕过平台访问禁令 ' + name)
      }
      if (ts.isIdentifier(node)) {
        const name = node.text
        if (relative !== uiProviderOwner && appBindings.has(name)
            && !ts.isImportSpecifier(node.parent)
            && !(ts.isPropertyAccessExpression(node.parent) && node.parent.expression === node && node.parent.name.text === 'useApp'))
          diagnostics.push(relative + ': Ant Design App 仅允许在 ' + uiProviderOwner + ' 挂载；页面只可调用 App.useApp()')
        if (name === 'getUserMedia')
          diagnostics.push(relative + ': 全源码禁止浏览器独立摄像头')
        if (coreLayers.has(from) && prohibitedCore.has(name))
          diagnostics.push(relative + ': 核心用例不得使用平台 API ' + name)
        if (viewLayers.has(from) && prohibitedIO.has(name))
          diagnostics.push(relative + ': 视图不得使用 HTTP/WebRTC/存储 I/O ' + name)
      }
      ts.forEachChild(node, visit)
    }
    visit(syntax)
  }

  return { files, diagnostics }
}

test('所有源码严格遵守依赖倒置与单一设备来源（无文件级豁免）', () => {
  const { files, diagnostics } = analyze(srcRoot)
  assert.ok(files.length > 0, '未扫描到产品源码')
  assert.deepEqual(diagnostics, [], diagnostics.join('\n'))
})

const EXPECTED_FIXTURE_VIOLATIONS = [
  'api/apiGlassSubpath.ts: 玻璃库仅允许由 components/GlassPanel.tsx 导入 (simple-liquid-glass/interactive)',
  'api/apiUi.ts: 基础设施不得依赖 UI 库 antd',
  'api/apiMarkdown.ts: Markdown 渲染库仅允许由 components/MarkdownContent.tsx 导入 (react-markdown)',
  'app/appGlass.tsx: 玻璃库仅允许由 components/GlassPanel.tsx 导入 (simple-liquid-glass/backdrop)',
  'app/appMarkdown.tsx: Markdown 渲染库仅允许由 components/MarkdownContent.tsx 导入 (remark-gfm)',
  'components/otherGlass.tsx: 玻璃库仅允许由 components/GlassPanel.tsx 导入 (simple-liquid-glass)',
  'components/antdInternal.ts: 禁止未审计的 Ant Design 子路径 antd/lib/button',
  'components/antdNamespace.ts: Ant Design 必须使用具名导入以检查全局配置所有权',
  'components/providerBarrel.ts: 禁止转发 Ant Design 全局配置能力 ConfigProvider',
  'components/otherMarkdown.tsx: Markdown 渲染库仅允许由 components/MarkdownContent.tsx 导入 (react-markdown)',
  'domain/markdownLeak.ts: 核心用例不允许外部运行时依赖 remark-parse',
  'domain/uiDate.ts: 核心用例不允许外部运行时依赖 dayjs',
  'pages/pageGlass.tsx: 玻璃库仅允许由 components/GlassPanel.tsx 导入 (simple-liquid-glass)',
  'pages/pageMarkdown.tsx: Markdown 渲染库仅允许由 components/MarkdownContent.tsx 导入 (react-markdown)',
  'pages/pageTheme.tsx: Ant Design 全局配置仅允许由 app/UiProvider.tsx 导入 (ConfigProvider)',
  'pages/pageApp.tsx: Ant Design App 仅允许在 app/UiProvider.tsx 挂载；页面只可调用 App.useApp()',
  'pages/vueRuntime.ts: Vue 仅作设计参考，禁止运行时依赖 vue',
  'pages/legacy.vue: Vue 仅作设计参考，禁止编译 Vue 组件',
]

test('反向测试：渲染/玻璃库越界导入必被文件级白名单拒绝', () => {
  const { files, diagnostics } = analyze(fixtureRoot)
  assert.ok(files.length > 0, '未扫描到反向夹具')
  assert.deepEqual(diagnostics.slice().sort(), EXPECTED_FIXTURE_VIOLATIONS.slice().sort(), diagnostics.join('\n'))
})

test('目标 UI 接入契约允许标准控件、主题读取与唯一玻璃所有者', () => {
  const { files, diagnostics } = analyze(validUiRoot)
  assert.ok(files.length > 0)
  assert.deepEqual(diagnostics, [], diagnostics.join('\n'))
})
