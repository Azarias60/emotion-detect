import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = fileURLToPath(new URL('../../src/', import.meta.url))
const urls = new Map()

// Execute real TypeScript through the installed compiler. Missing capabilities
// fail inside their test, so the remaining cases can still be collected.
export async function loadModule(relative) {
  function compile(filename) {
    if (urls.has(filename)) return urls.get(filename)
    assert.ok(fs.existsSync(filename), `待实现的业务入口：${path.relative(root, filename)}`)
    const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    // Vite 注入的环境对象属于运行环境；只在测试转译时提供，不替换网络或业务。
    const code = outputText.replace(/import\.meta\.env\b/g, '({})').replace(/\bfrom\s*['"]([^'"]+)['"]/g, (match, specifier) => {
      if (!specifier.startsWith('.')) return `from ${JSON.stringify(import.meta.resolve(specifier))}`
      const resolved = path.resolve(path.dirname(filename), specifier)
      const target = [resolved, resolved + '.ts', resolved + '.tsx', path.join(resolved, 'index.ts')]
        .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile())
      assert.ok(target, `业务模块导入未落实：${specifier}`)
      return `from ${JSON.stringify(compile(target))}`
    })
    const url = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
    urls.set(filename, url)
    return url
  }
  return import(compile(path.join(root, relative)))
}

export function requiredFunction(module, name) {
  assert.equal(typeof module[name], 'function', `待实现的消费契约：${name}`)
  return module[name]
}
