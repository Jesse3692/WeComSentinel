<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { avatarRetryKey } from './avatarRetry'
const props = defineProps<{name?:string,mediaId?:number,url?:string,large?:boolean,warm?:boolean}>()
const failed = ref(false)
const source = computed(()=>props.mediaId?'/api/v1/media/'+props.mediaId:props.url?.startsWith('https://')?props.url:'')
watch(source,()=>failed.value=false)
const retry = inject(avatarRetryKey, ref(0))
watch(retry,()=>{if(failed.value)failed.value=false})
</script>
<template><div class="avatar" :class="{large,warm}"><img v-if="source && !failed" :src="source" alt="头像" @error="failed=true"><span v-else>{{(name || '?').slice(0,1)}}</span></div></template>
