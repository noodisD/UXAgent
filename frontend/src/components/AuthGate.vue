<template>
  <div class="gate">
    <form class="gate-card" @submit.prevent="submit">
      <div class="gate-brand">
        <div class="brand-mark"><div class="brand-slash"></div></div>
        <div class="gate-title">UXAgent</div>
      </div>

      <p class="gate-lead">{{ t('auth.lead') }}</p>

      <label class="ux-label" for="gate-key">{{ t('auth.password') }}</label>
      <input
        id="gate-key"
        ref="input"
        v-model="key"
        class="gate-input"
        type="password"
        autocomplete="current-password"
        :disabled="checking"
        :placeholder="t('auth.placeholder')"
      />

      <div v-if="error" class="gate-error">{{ error }}</div>

      <button class="btn btn-primary gate-btn" type="submit" :disabled="checking || !key.trim()">
        {{ checking ? t('auth.checking') : t('auth.submit') }}
      </button>

      <div class="gate-foot mono">{{ apiOrigin() }}</div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiOrigin, setKey, verifyKey } from '../api'
import { useI18n } from '../i18n'

const emit = defineEmits<{ (e: 'unlocked'): void }>()
const { t } = useI18n()

const key = ref('')
const error = ref<string | null>(null)
const checking = ref(false)
const input = ref<HTMLInputElement | null>(null)

onMounted(() => input.value?.focus())

async function submit() {
  const candidate = key.value.trim()
  if (!candidate || checking.value) return
  checking.value = true
  error.value = null
  // A rejected password and an unreachable backend look identical from here if
  // we only said "failed" — verifyKey returns false for both, so the message
  // has to name both possibilities rather than blame the user.
  const ok = await verifyKey(candidate)
  checking.value = false
  if (!ok) {
    error.value = t('auth.error')
    key.value = ''
    input.value?.focus()
    return
  }
  setKey(candidate)
  emit('unlocked')
}
</script>

<style scoped lang="scss">
.gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); padding: 24px; }
.gate-card { width: 100%; max-width: 380px; background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 30px 28px; display: flex; flex-direction: column; gap: 10px; }
.gate-brand { display: flex; align-items: center; gap: 11px; margin-bottom: 6px; }
.brand-mark { position: relative; width: 26px; height: 26px; border-radius: 7px; background: var(--text); overflow: hidden; flex: none; }
.brand-slash { position: absolute; width: 170%; height: 8px; background: var(--ac); transform: rotate(-25deg); top: 9px; left: -18%; }
.gate-title { font-family: var(--expanded); font-weight: 700; font-size: 17px; letter-spacing: .02em; text-transform: uppercase; }
.gate-lead { font-size: 13px; color: var(--text-3); line-height: 1.5; margin: 0 0 6px; }
.ux-label { font: 600 11px var(--mono); text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); }
.gate-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border-input); border-radius: 9px; background: var(--surface); color: var(--text); font: 400 14px var(--sans); }
.gate-input:focus { outline: 2px solid var(--ac); outline-offset: 1px; }
.gate-error { font-size: 12.5px; color: var(--ac); line-height: 1.45; }
.gate-btn { margin-top: 6px; justify-content: center; }
.gate-foot { font-size: 11px; color: var(--text-faint); text-align: center; margin-top: 4px; }
</style>
