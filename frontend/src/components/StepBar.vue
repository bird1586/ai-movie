<script setup lang="ts">
import { NStep, NSteps } from 'naive-ui'

defineProps<{ current: number }>()

const STEPS = ['劇本與分鏡', '資產聖經', '場景與世界觀', '配音與時間軸', '3D 運鏡預視', '渲染與成片']
</script>

<template>
  <NSteps :current="current" size="small">
    <NStep v-for="s in STEPS" :key="s" :title="s" />
  </NSteps>
</template>
