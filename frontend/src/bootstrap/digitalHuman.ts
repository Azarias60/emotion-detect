import { connectObjectStream, createConversationGateway, createModelConfigGateway, createObjectGateway, createProductionGateway } from '../api/digitalHuman'
import { createObjectCollection } from '../application/digitalHuman'
import type { ObjectGateway } from '../api/digitalHuman'
import type { ObjectKind } from '../domain/digitalHuman'

export { connectObjectStream, createConversationGateway, createModelConfigGateway, createObjectGateway, createProductionGateway }

import type { SessionGateways } from '../api/digitalHuman'
import type { ObjectCollection } from '../application/digitalHuman'

export type { SessionGateways }

export type AssembledCollections = Record<'configuration' | 'avatar' | 'voice' | 'material', ObjectCollection>

export function assembleSessionGateways(token: string): SessionGateways {
  return {
    objects: createObjectGateway(token),
    conversation: createConversationGateway(token),
    modelConfig: createModelConfigGateway(token),
    production: createProductionGateway(token),
  }
}

export function assembleObjectCollections(gateways: SessionGateways, token: string): {
  configuration: ObjectCollection
  avatar: ObjectCollection
  voice: ObjectCollection
  material: ObjectCollection
  stop: () => void
} {
  const collection = (kind: ObjectKind) =>
    createObjectCollection(gateways.objects, kind, (k, onRows) => connectObjectStream(token, k, onRows))
  const collections = {
    configuration: collection('configuration'),
    avatar: collection('avatar'),
    voice: collection('voice'),
    material: collection('material'),
  }
  return {
    ...collections,
    stop: () => Object.values(collections).forEach(item => item.stop()),
  }
}

// ── 引擎能力端口（feat/digital-human-merge 标准）：数字人引擎实时会话、
//    照片制作产线（8081）、语音克隆（8081）、模型 Key（引擎热配置）──
import { createAvatarStudioPort } from '../api/avatarStudio'
import { createVoiceClonePort } from '../api/voiceClone'
import { createLlmSettingsPort } from '../api/llmSettings'
import { createDigitalHumanTransport, createGuideTransport } from '../api/engineTransports'

export { createDigitalHumanTransport, createGuideTransport, createAvatarStudioPort, createVoiceClonePort, createLlmSettingsPort }
