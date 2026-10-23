<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  dashboard: 'M4 20V10h4v10M10 20V4h4v16M16 20V7h4v13M3 20h18',
  added: 'M12 5v14M5 12h14',
  deleted: 'M5 12h14',
  groups: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-1 1v-9.5a9 9 0 0 1 18 0ZM8 11h.01M12 11h.01M16 11h.01',
  me: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2',
}
</script>

<template>
  <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path :d="paths[name]" />
  </svg>
</template>

<style scoped>
.nav-icon { width:22px; height:22px; flex-shrink:0; display:block }
</style>
