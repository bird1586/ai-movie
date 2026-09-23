<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, dateZhTW, zhTW } from 'naive-ui'
</script>

<template>
  <NConfigProvider :locale="zhTW" :date-locale="dateZhTW">
    <NMessageProvider>
      <NDialogProvider>
        <RouterView />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
