// 反向测试夹具（不参与构建/运行）：components 层除 GlassPanel.tsx 外的文件导入玻璃库，必须被门禁拒绝。
import { LiquidGlass } from 'simple-liquid-glass'

export const leak = LiquidGlass
