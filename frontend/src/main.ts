import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import './style.css'
import App from './App.vue'
import ProjectList from './views/ProjectList.vue'
import Wizard from './views/Wizard.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: ProjectList },
    { path: '/p/:id/wizard', component: Wizard, props: true },
  ],
})

createApp(App).use(router).mount('#app')
