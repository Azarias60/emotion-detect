/** 数字人对象的派生关系：当前配置与有效资源从集合及选择派生。

解析是纯函数：临时音色优先于配置默认音色；引用缺失呈现为不可用，
不改写引用或自动替换对象。重新选择配置时清除临时音色由页面表达。
*/
export type ObjectKind = 'configuration' | 'avatar' | 'voice' | 'material'

export type DigitalHumanObject = { id: string | number; name?: string; [key: string]: unknown }

export type DigitalHumanSelectionView = {
  selectedConfigId: string
  temporaryVoiceId: string
}

export type CompanionSelection = {
  configs: DigitalHumanObject[]
  avatars: DigitalHumanObject[]
  voices: DigitalHumanObject[]
  selectedConfigId: string
  temporaryVoiceId: string
}

export type ResolvedCompanion = {
  configuration: DigitalHumanObject | null
  avatar: DigitalHumanObject | null
  voice: DigitalHumanObject | null
}

function byId(items: DigitalHumanObject[], id: string | number | undefined | null) {
  if (id === undefined || id === null || id === '') return null
  // 对象标识在集合中可能是数字或字符串；派生按同一值的字符串形式比较。
  return items.find(item => String(item.id) === String(id)) ?? null
}

export function resolveCompanion(selection: CompanionSelection): ResolvedCompanion {
  const configuration = byId(selection.configs, selection.selectedConfigId)
  return {
    configuration,
    avatar: byId(selection.avatars, configuration?.avatar as string | undefined),
    voice: byId(selection.voices, selection.temporaryVoiceId || (configuration?.voice as string | undefined)),
  }
}
