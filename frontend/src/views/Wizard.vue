<script setup lang="ts">
import { NAlert, NButton, NCard, NGrid, NGridItem, NSpace, NTag } from 'naive-ui'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, connectEvents, type Project, type Shot, type WhatIf } from '../api'
import BudgetPanel from '../components/BudgetPanel.vue'
import StepBar from '../components/StepBar.vue'
import StoryboardStep from '../components/StoryboardStep.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()
const project = ref<Project>()
const shots = ref<Shot[]>([])
const whatIf = ref<WhatIf[]>([])
let ws: WebSocket | undefined

const g1 = computed(() => project.value?.gate_list.find((g) => g.id === 'G1'))
const laterGates = computed(() => project.value?.gate_list.filter((g) => g.step > 1) ?? [])

async function load() {
  ;[project.value, shots.value, whatIf.value] = await Promise.all([
    api.getProject(props.id),
    api.listShots(props.id),
    api.whatIf(props.id),
  ])
}

onMounted(async () => {
  await load()
  ws = connectEvents(props.id, (ev) => {
    if (String(ev.type).startsWith('gate.') || ev.type === 'budget.threshold') load()
  })
})
onUnmounted(() => ws?.close())
</script>

<template>
  <div v-if="project" class="page">
    <NSpace justify="space-between" align="center">
      <h2>🎬 {{ project.name }} <NTag>步驟 {{ project.step }}/6</NTag></h2>
      <NButton text @click="router.push('/')">◀ 回專案清單</NButton>
    </NSpace>
    <StepBar :current="project.step" />

    <NGrid :cols="3" :x-gap="16" responsive="screen" item-responsive style="margin-top: 16px">
      <NGridItem span="3 m:2">
        <NSpace vertical>
          <StoryboardStep v-if="g1" :project-id="id" :shots="shots" :gate="g1" @changed="load" />
          <NCard title="後續門檻" size="small">
            <NAlert type="default" :show-icon="false" style="margin-bottom: 8px">
              步驟 2–6 需要 GPU，目前還沒接上。
            </NAlert>
            <NSpace>
              <NTag v-for="g in laterGates" :key="g.id" :type="g.status === 'approved' ? 'success' : 'default'">
                {{ g.id }} {{ g.name }}
              </NTag>
            </NSpace>
          </NCard>
        </NSpace>
      </NGridItem>
      <NGridItem span="3 m:1">
        <BudgetPanel :project="project" :what-if="whatIf" />
      </NGridItem>
    </NGrid>
  </div>
</template>

<style scoped>
.page { max-width: 1280px; margin: 0 auto; padding: 24px; }
</style>
