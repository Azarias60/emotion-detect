// 反向测试夹具（不参与构建/运行）：pages 页面层直连玻璃库而不经过 GlassPanel，必须被门禁拒绝。
import { LiquidGlass } from 'simple-liquid-glass'

export const leak = LiquidGlass
