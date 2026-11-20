import { httpAuth } from '../api/auth'
import { browserSessionStore } from '../api/sessionStore'
import { createSessionService } from '../domain/auth'

export const auth = createSessionService(httpAuth, browserSessionStore)
