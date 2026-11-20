// 反向测试夹具（不参与构建/运行）：app 层绕过 GlassPanel 导入玻璃库入口，必须被门禁拒绝。
import { LiquidGlassScene } from 'simple-liquid-glass/backdrop'

export const leak = LiquidGlassScene
