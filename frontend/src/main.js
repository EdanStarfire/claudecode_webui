import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'

// Import existing styles from static/
import './assets/styles.css'

// Import shared tool theme tokens
import './assets/tool-theme.css'

// Create Vue app
const app = createApp(App)

// Install Pinia (state management)
app.use(createPinia())

// Install Vue Router
app.use(router)

// Mount app
app.mount('#app')
