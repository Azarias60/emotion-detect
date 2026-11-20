// 反向测试夹具（不参与构建/运行）：pages 页面层导入渲染库（旧版层级豁免会放行的形态），必须被门禁拒绝。
import Markdown from 'react-markdown'

export const leak = Markdown
