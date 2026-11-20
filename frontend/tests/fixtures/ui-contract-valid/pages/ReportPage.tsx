import { App, Button, DatePicker, Table, theme } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { Bubble } from '@ant-design/x'
export function ReportPage() {
  const { token } = theme.useToken()
  const { message } = App.useApp()
  return <Button icon={<SearchOutlined />} style={{ color: token.colorText }}>{dayjs().format('YYYY-MM-DD')}</Button>
}
