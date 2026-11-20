import type { GuideDoc } from '../domain/guideSession'

/**
 * 资料整理卡（资料栏常驻）：引擎每次回答会把该回答的要点推进来替换内容，
 * 不是埋在聊天流里被刷走。风格沿用原版 DocCard：编号要点 + 扫描线 + 四角描角。
 */
export function GuideDocCard({ doc }: { doc: GuideDoc | null }) {
  if (!doc) return null
  const points = doc.points.slice(0, 8)
  return <section className="gdc" aria-label="资料整理">
    <header className="gdc-head">
      <span className="gdc-badge">资料</span>
      <h4>{doc.title}</h4>
    </header>
    {doc.desc && <p className="gdc-desc">{doc.desc}</p>}
    <ol className="gdc-points">
      {points.map((point, index) => (
        <li key={index}>
          <i>{String(index + 1).padStart(2, '0')}</i>
          <span>{point}</span>
        </li>
      ))}
    </ol>
    <footer className="gdc-foot"><small>由数字人整理 · 仅供参考</small></footer>
    <span className="gdc-corner tl" aria-hidden="true" />
    <span className="gdc-corner tr" aria-hidden="true" />
    <span className="gdc-corner bl" aria-hidden="true" />
    <span className="gdc-corner br" aria-hidden="true" />
  </section>
}
