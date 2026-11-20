import { WorkMessage } from '../components/WorkSurface'

export function PlaceholderPage() {
  return <section className="placeholder-page">
    <WorkMessage title="页面不存在" detail="没有找到此地址。"><a href="/">返回首页</a></WorkMessage>
  </section>
}
