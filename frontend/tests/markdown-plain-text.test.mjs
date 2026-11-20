import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'

const source = fs.readFileSync(new URL('../src/components/MarkdownContent.tsx', import.meta.url), 'utf8')
let code = ts.transpileModule(source, {
  fileName: 'MarkdownContent.tsx',
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
    // Compile the JSX to React.createElement calls; the test never invokes the component itself.
    jsx: ts.JsxEmit.React,
  },
}).outputText

// A data: module has no package lookup path; resolve the actual installed dependencies.
for (const name of ['react', 'react-markdown', 'mdast-util-to-string', 'remark-gfm', 'remark-parse', 'unified']) {
  code = code.replace(`from '${name}'`, `from ${JSON.stringify(import.meta.resolve(name))}`)
}
const { markdownToPlainText } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

test('历史列表摘要去除标题、强调、链接与列表格式，保留可读文字', () => {
  const input = '## 近期摘要\n\n- **心率**：\`72 bpm\`\n- [建议复查](https://example.com/report)'
  assert.equal(markdownToPlainText(input), '近期摘要 心率：72 bpm 建议复查')
})

test('GFM 表格与围栏代码的正文不丢失', () => {
  const table = '| 指标 | 结果 |\n| --- | --- |\n| HRV | **正常** |'
  assert.equal(markdownToPlainText(table), '指标 / 结果 HRV / 正常')
  assert.equal(markdownToPlainText('\`\`\`json\n{"score":72}\n\`\`\`'), '{"score":72}')
})

test('原始 HTML 不进入摘要、空摘要安全、纯文本保持不变', () => {
  assert.equal(markdownToPlainText('<script>恶意内容</script>\n\n**安全**'), '安全')
  assert.equal(markdownToPlainText('   '), '')
  assert.equal(markdownToPlainText('本轮健康检测表现良好'), '本轮健康检测表现良好')
})
