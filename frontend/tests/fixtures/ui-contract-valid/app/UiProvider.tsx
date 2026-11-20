import { ConfigProvider, App as AntApp, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import 'antd/dist/reset.css'
export function UiProvider({ children }) {
  return <ConfigProvider locale={zhCN} theme={{ algorithm: theme.darkAlgorithm }}>
    <AntApp>{children}</AntApp>
  </ConfigProvider>
}
