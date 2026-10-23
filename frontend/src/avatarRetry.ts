import type { InjectionKey, Ref } from 'vue'

export const avatarRetryKey: InjectionKey<Ref<number>> = Symbol('avatar-retry')
