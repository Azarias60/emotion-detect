import { Button, Descriptions } from 'antd'
import { SectionHeading } from '../components/WorkSurface'

export default function AccountPage({ userName, onLogout }: { userName: string; onLogout: () => void }) {
  return <section className="workbench-stack" id="account">
    <SectionHeading title="登录会话" />
      <Descriptions column={1} items={[{ key: 'user', label: '当前用户名', children: userName }]} />
      <Button danger className="inline-action" onClick={onLogout}>退出登录</Button>
  </section>
}
