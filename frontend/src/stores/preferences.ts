import { defineStore } from 'pinia'
import { ref } from 'vue'

export const usePreferencesStore = defineStore('preferences', () => {
  const includeClosed = ref(false)
  const redUp = ref(true)
  const toggleClosed = () => { includeClosed.value = !includeClosed.value }
  return { includeClosed, redUp, toggleClosed }
})
