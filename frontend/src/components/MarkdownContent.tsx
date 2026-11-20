import Markdown from 'react-markdown'
import type { Nodes } from 'mdast'
import { toString } from 'mdast-util-to-string'
import remarkGfm from 'remark-gfm'
import remarkParse from 'remark-parse'
import { unified } from 'unified'

/** Single Markdown grammar shared by the renderer and the plain-text preview. */
const remarkPlugins = [remarkGfm]
const parser = unified().use(remarkParse).use(remarkPlugins)
type MarkdownNode = Nodes

function plainNode(node: MarkdownNode): string {
  if (node.type === 'html' || node.type === 'thematicBreak') return ''
  if (node.type === 'table') {
    return node.children.map(row =>
      row.children.map(cell => plainNode(cell)).filter(Boolean).join(' / ')
    ).filter(Boolean).join(' ')
  }
  if (node.type === 'list' || node.type === 'listItem' || node.type === 'blockquote') {
    return node.children.map(child => plainNode(child)).filter(Boolean).join(' ')
  }
  if ('children' in node) return node.children.map(child => plainNode(child)).join('')
  return toString(node)
}

/** Strip Markdown syntax without losing code, list items or GFM table contents. */
export function markdownToPlainText(source: string): string {
  if (!source.trim()) return ''
  return parser.parse(source).children
    .map(node => plainNode(node))
    .filter(Boolean)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Structured report payloads render as a fenced JSON block. */
export function jsonMarkdown(data: object): string {
  return '```json\n' + JSON.stringify(data, null, 2) + '\n```'
}

export function MarkdownContent({ source, className = '' }: {
  source: string
  className?: string
}) {
  return <div className={`markdown-content ${className}`}>
    <Markdown skipHtml remarkPlugins={remarkPlugins} disallowedElements={['img']}>
      {source}
    </Markdown>
  </div>
}
