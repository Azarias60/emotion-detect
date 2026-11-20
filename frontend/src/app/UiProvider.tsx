import { App as AntApp, theme } from 'antd'
import { XProvider } from '@ant-design/x'
import zhCNX from '@ant-design/x/locale/zh_CN'
import zhCN from 'antd/locale/zh_CN'
import 'antd/dist/reset.css'
import type { ReactNode } from 'react'

const accent = '#60a5fa'

/** 统一主题与全局反馈的唯一配置所有者：字体、主色、圆角走统一规范。 */
export function UiProvider({ children }: { children: ReactNode }) {
  return <XProvider
    locale={{ ...zhCNX, ...zhCN }}
    button={{ autoInsertSpace: false }}
    theme={{
      algorithm: theme.darkAlgorithm,
      token: {
        colorPrimary: '#3b82f6',
        colorBgLayout: '#0a1424',
        colorBgContainer: '#0f1c30',
        colorBgElevated: '#13223a',
        colorBorder: '#26406a',
        colorBorderSecondary: '#1e3456',
        colorText: '#e8f0fb',
        colorTextSecondary: '#9fb6d4',
        colorTextTertiary: '#7f97b8',
        colorTextPlaceholder: '#6f87a8',
        colorLink: accent,
        fontFamily: '"PingFang SC", "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif',
        borderRadius: 12,
        fontSize: 14,
        // A popup must remain readable even when the glass scene delays animation frames.
        motion: false,
      },
      components: {
        Tabs: { itemSelectedColor: accent, itemHoverColor: accent, inkBarColor: accent },
        Pagination: { itemActiveColor: accent },
      },
    }}>
    <AntApp>{children}</AntApp>
  </XProvider>
}
