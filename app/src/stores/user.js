import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('emotion-token') || '')
  const user = ref(JSON.parse(localStorage.getItem('emotion-user') || 'null'))
  const isLoggedIn = computed(() => Boolean(token.value))

  function setSession(nextToken, nextUser) {
    token.value = nextToken
    user.value = nextUser
    localStorage.setItem('emotion-token', nextToken)
    localStorage.setItem('emotion-user', JSON.stringify(nextUser || null))
  }
  function setUser(nextUser) {
    user.value = nextUser
    localStorage.setItem('emotion-user', JSON.stringify(nextUser || null))
  }
  function clearSession() {
    token.value = ''
    user.value = null
    localStorage.removeItem('emotion-token')
    localStorage.removeItem('emotion-user')
  }
  return { token, user, isLoggedIn, setSession, setUser, clearSession }
})
