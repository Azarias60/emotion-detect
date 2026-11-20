import { useState } from 'react'
import { Alert, Button, Form, Input, Tabs } from 'antd'
import { Activity } from 'lucide-react'
import { SectionHeading } from '../components/WorkSurface'

// 登录/注册表单是纯展示层：只有 UI 状态与回调，不持有账号/HTTP/会话业务。
export default function LoginPage({ busy, error, message, onLogin, onRegister }: {
  busy: boolean; error: string; message: string
  onLogin: (username: string, password: string) => Promise<void>
  onRegister: (username: string, password: string, confirm: string) => Promise<boolean>
}) {
  const [form] = Form.useForm()
  const [mode, setMode] = useState<'login' | 'register'>('login')

  const commonRules = {
    username: [{ required: true, message: '请输入账号' }, { min: 3, max: 16, message: '账号长度为 3–16 个字符' }],
    password: [{ required: true, message: '请输入密码' }, { min: 5, max: 64, message: '密码长度为 5–64 个字符' }],
  }

  const loginForm = <Form form={form} layout="vertical" onFinish={({ username, password }) => void onLogin(username, password)}>
    <Form.Item name="username" label="账号" rules={commonRules.username}>
      <Input autoComplete="username" placeholder="输入账号" />
    </Form.Item>
    <Form.Item name="password" label="密码" rules={commonRules.password}>
      <Input.Password autoComplete="current-password" placeholder="输入密码" />
    </Form.Item>
    <Button type="primary" htmlType="submit" block loading={busy}>
      登录
    </Button>
  </Form>

  const registerForm = <Form layout="vertical" onFinish={async ({ username, password, rePassword }) => {
    if (await onRegister(username, password, rePassword)) {
      form.setFieldsValue({ username, password: undefined })
      setMode('login')
    }
  }}>
    <Form.Item name="username" label="账号" rules={commonRules.username}>
      <Input autoComplete="username" placeholder="输入账号" />
    </Form.Item>
    <Form.Item name="password" label="密码" rules={commonRules.password}>
      <Input.Password autoComplete="new-password" placeholder="输入密码" />
    </Form.Item>
    <Form.Item name="rePassword" label="确认密码"
      dependencies={['password']}
      rules={[{ required: true, message: '请再次输入密码' },
        ({ getFieldValue }) => ({
          validator: (_, value) => value === getFieldValue('password')
            ? Promise.resolve() : Promise.reject(new Error('两次输入的密码不一致')),
        })]}>
      <Input.Password autoComplete="new-password" placeholder="再次输入密码" />
    </Form.Item>
    <Button type="primary" htmlType="submit" block loading={busy}>创建账号</Button>
  </Form>

  return <section className="workbench-stack" id="login-panel">
    <SectionHeading title="欢迎来到 PhysioSense">
      <Activity size={22} strokeWidth={1.75} aria-hidden="true" />
    </SectionHeading>
    <Tabs activeKey={mode} onChange={key => setMode(key as 'login' | 'register')}
      items={[
        { key: 'login', label: '登录', children: loginForm },
        { key: 'register', label: '注册', children: registerForm },
      ]} />
    {(error || message) && <Alert type={error ? 'error' : 'success'} showIcon
      message={error || message} role="status" />}
  </section>
}
