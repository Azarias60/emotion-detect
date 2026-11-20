// 反向测试夹具（不参与构建/运行）：components 层除 MarkdownContent.tsx 外的文件导入渲染库，必须被门禁拒绝。
import Markdown from 'react-markdown'

export const leak = Markdown
