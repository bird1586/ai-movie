<script setup lang="ts">
import { NButton, NCard, NEmpty, NForm, NFormItem, NInput, NInputNumber, NSpace, NTag, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Project } from '../api'

const router = useRouter()
const message = useMessage()
const projects = ref<Project[]>([])
const form = ref({ name: '', target_s: 60, budget_cap: 1000 })

async function load() {
  projects.value = await api.listProjects()
}

async function create() {
  if (!form.value.name.trim()) return message.warning('請輸入專案名稱')
  try {
    const p = await api.createProject(form.value)
    router.push(`/p/${p.id}/wizard`)
  } catch (e) {
    message.error((e as Error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <h1>🎬 AI 動畫工作室</h1>
    <NCard title="開新專案">
      <NForm inline label-placement="left">
        <NFormItem label="專案名稱">
          <NInput v-model:value="form.name" placeholder="冰封峽谷預告片" />
        </NFormItem>
        <NFormItem label="片長（秒）">
          <NInputNumber v-model:value="form.target_s" :min="5" :max="3600" />
        </NFormItem>
        <NFormItem label="預算上限（NT$）">
          <NInputNumber v-model:value="form.budget_cap" :min="50" />
        </NFormItem>
        <NButton type="primary" @click="create">建立</NButton>
      </NForm>
    </NCard>

    <h2>我的專案</h2>
    <NEmpty v-if="!projects.length" description="還沒有專案" />
    <NSpace vertical>
      <NCard
        v-for="p in projects"
        :key="p.id"
        hoverable
        class="project"
        @click="router.push(`/p/${p.id}/wizard`)"
      >
        <NSpace justify="space-between" align="center">
          <strong>{{ p.name }}</strong>
          <NSpace>
            <NTag>步驟 {{ p.step }}/6</NTag>
            <NTag :type="p.budget.within_budget ? 'success' : 'error'">
              預估 NT$ {{ p.estimate.total }} / {{ p.budget_cap }}
            </NTag>
          </NSpace>
        </NSpace>
      </NCard>
    </NSpace>
  </div>
</template>

<style scoped>
.page { max-width: 960px; margin: 0 auto; padding: 24px; }
.project { cursor: pointer; }
</style>
