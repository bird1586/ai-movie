<script setup lang="ts">
import { NAlert, NButton, NCard, NDataTable, NInput, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { computed, h, ref } from 'vue'
import { api, type GateView, type Shot } from '../api'

const props = defineProps<{ projectId: string; shots: Shot[]; gate: GateView }>()
const emit = defineEmits<{ changed: [] }>()
const message = useMessage()
const script = ref('')
const busy = ref(false)

const columns: DataTableColumns<Shot> = [
  { title: '#', key: 'shot_id', width: 90 },
  { title: '畫面', key: 'description' },
  { title: '對白', key: 'lip_sync', width: 70, render: (s) => (s.dialogue ? '🗣 有' : '無') },
  {
    title: '秒數 / 幀數',
    key: 'duration',
    width: 150,
    render: (s) =>
      h(NSpace, { size: 4 }, () => [
        `${s.duration.total_s}s / ${s.duration.frames}f`,
        s.needs_split ? h(NTag, { type: 'error', size: 'small' }, () => '太長，要拆') : null,
      ]),
  },
  {
    title: '類型',
    key: 'track',
    width: 90,
    render: (s) => (s.track === 'A' ? '🔥 快' : '🧊 慢'),
  },
]

const summary = computed(() => {
  const a = props.shots.filter((s) => s.track === 'A').length
  const talk = props.shots.filter((s) => s.dialogue).length
  return `🔥${a} 🧊${props.shots.length - a} 🗣${talk}`
})

async function parse() {
  if (!script.value.trim()) return message.warning('先貼上劇本')
  busy.value = true
  try {
    await api.parseStoryboard(props.projectId, script.value)
    emit('changed')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function act(action: 'approve' | 'reject' | 'reopen') {
  try {
    const r = await api.gate(props.projectId, 'G1', action)
    if (r.invalidated.length) message.warning(`已回頭，${r.invalidated.join('、')} 需要重新確認`)
    emit('changed')
  } catch (e) {
    message.error((e as Error).message)
  }
}
</script>

<template>
  <NCard title="[1] 劇本與分鏡拆解">
    <NSpace vertical>
      <NInput
        v-model:value="script"
        type="textarea"
        :rows="5"
        placeholder="貼上你的劇本（想到什麼寫什麼，AI 會整理）"
        :disabled="gate.status === 'approved'"
      />
      <NButton type="primary" :loading="busy" :disabled="gate.status === 'approved'" @click="parse">
        ✨ 幫我拆成分鏡
      </NButton>
      <NAlert type="info" :show-icon="false">
        目前是示範版拆解（一句一鏡），正式版會換成 AI 拆解。「🔥快」＝動作大、比較貴；「🧊慢」＝比較省。
      </NAlert>

      <NDataTable v-if="shots.length" :columns="columns" :data="shots" size="small" />

      <NCard v-if="gate.status === 'ready'" size="small" class="gate">
        <strong>★ G1 確認：分鏡表 OK 嗎？</strong>
        <div>目前配置 {{ summary }}</div>
        <NSpace style="margin-top: 8px">
          <NButton type="primary" @click="act('approve')">👍 確認，去建資產</NButton>
          <NButton @click="act('reject')">🔄 重新拆</NButton>
        </NSpace>
      </NCard>
      <NCard v-else-if="gate.status === 'approved'" size="small" class="gate">
        ✅ 分鏡表已確認。
        <NButton text type="warning" @click="act('reopen')">回頭修改（後面已確認的步驟要重看）</NButton>
      </NCard>
    </NSpace>
  </NCard>
</template>

<style scoped>
.gate { border: 2px solid #f0a020; }
</style>
