<script setup lang="ts">
import { NCard, NDivider, NProgress, NSpace, NText } from 'naive-ui'
import { computed } from 'vue'
import type { Project, WhatIf } from '../api'

const props = defineProps<{ project: Project; whatIf: WhatIf[] }>()

const LEVEL_TEXT = { ok: '✅ 預算內', warn: '⚠️ 已用 80%', alert: '⚠️ 已用 90%', hold: '💰 達上限，先暫停' }
const status = computed(() => {
  const b = props.project.budget
  return { text: LEVEL_TEXT[b.level], type: b.level === 'ok' ? 'success' : b.level === 'warn' ? 'warning' : 'error' } as const
})
</script>

<template>
  <NCard title="📊 預算儀表板" size="small">
    <NSpace vertical :size="4">
      <div>已用 NT$ {{ project.budget.used }} / {{ project.budget_cap }}</div>
      <NProgress
        type="line"
        :percentage="Math.min(100, Math.round(project.budget.ratio * 100))"
        :status="status.type"
      />
      <NText :type="status.type">{{ status.text }}</NText>
      <NDivider style="margin: 8px 0" />
      <div>預估總額 NT$ {{ project.estimate.total }}
        <NText :type="project.budget.within_budget ? 'success' : 'error'">
          {{ project.budget.within_budget ? '✅ 在預算內' : '❌ 會超出預算' }}
        </NText>
      </div>
      <NText depth="3">
        製作 {{ project.estimate.production }} ｜ 資產 {{ project.estimate.assets }} ｜ 開機預留 {{ project.estimate.overhead }}
      </NText>
      <template v-if="whatIf.length">
        <NDivider style="margin: 8px 0" />
        <strong>🧮 如果…會怎樣</strong>
        <div v-for="w in whatIf" :key="w.label">
          {{ w.label }} →
          <NText :type="w.saving >= 0 ? 'success' : 'warning'">
            {{ w.saving >= 0 ? `省 NT$${w.saving}` : `多花 NT$${-w.saving}` }}
          </NText>
        </div>
      </template>
    </NSpace>
  </NCard>
</template>
