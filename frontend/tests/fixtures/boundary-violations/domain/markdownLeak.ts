// 反向测试夹具（不参与构建/运行）：domain 核心层导入渲染库，必须被门禁拒绝。
import { remarkParse } from 'remark-parse'

export const leak = remarkParse
