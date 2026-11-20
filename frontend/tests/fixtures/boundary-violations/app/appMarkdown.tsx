// 反向测试夹具（不参与构建/运行）：app 组合层导入渲染库，必须被门禁拒绝。
import { remarkGfm } from 'remark-gfm'

export const leak = remarkGfm
