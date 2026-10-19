<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { setAuth } from '../utils/auth'

const route = useRoute()
const router = useRouter()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  if (username.value.trim().length < 3) {
    error.value = '用户名至少 3 个字符'
    return
  }
  if (password.value.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  busy.value = true
  try {
    const fn = mode.value === 'login' ? api.login : api.register
    const r = await fn(username.value.trim(), password.value)
    setAuth(r.access_token, r.username)
    const to = (route.query.redirect as string) || '/chat'
    router.replace(to)
  } catch (e) {
    error.value =
      e instanceof Error ? e.message.replace(/^\d+:\s*/, '') : '操作失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="login-card" @submit.prevent="submit">
      <div class="login-brand">
        <div class="login-mark">L</div>
        <div>
          <h1>LabPilot</h1>
          <p>课题组科研事务助理</p>
        </div>
      </div>

      <div class="tabs">
        <button
          type="button"
          :class="{ active: mode === 'login' }"
          @click="mode = 'login'; error = ''"
        >
          登录
        </button>
        <button
          type="button"
          :class="{ active: mode === 'register' }"
          @click="mode = 'register'; error = ''"
        >
          注册
        </button>
      </div>

      <label class="field">
        <span>用户名</span>
        <input v-model="username" autocomplete="username" placeholder="至少 3 个字符" />
      </label>
      <label class="field">
        <span>密码</span>
        <input
          v-model="password"
          type="password"
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          placeholder="至少 6 位"
        />
      </label>

      <p v-if="error" class="login-error">{{ error }}</p>

      <button class="login-btn" type="submit" :disabled="busy">
        {{ busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(120% 70% at 0% 0%, rgba(79, 140, 255, 0.18), transparent 60%),
    linear-gradient(180deg, #0c1729 0%, #0f1e38 100%);
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border-radius: 16px;
  padding: 30px 28px;
  box-shadow: 0 24px 60px rgba(4, 12, 28, 0.4);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}

.login-mark {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #3b74e0, #6ea3ff);
  color: #fff;
  font-weight: 700;
  font-size: 22px;
}

.login-brand h1 {
  margin: 0;
  font-size: 20px;
  color: #101a2e;
}

.login-brand p {
  margin: 2px 0 0;
  font-size: 12px;
  color: #6b7890;
}

.tabs {
  display: flex;
  gap: 6px;
  background: #f1f4fa;
  border-radius: 10px;
  padding: 4px;
  margin-bottom: 18px;
}

.tabs button {
  flex: 1;
  padding: 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #5a6780;
  font-size: 14px;
  cursor: pointer;
}

.tabs button.active {
  background: #fff;
  color: #2b5fd0;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(20, 40, 80, 0.1);
}

.field {
  display: block;
  margin-bottom: 14px;
}

.field span {
  display: block;
  font-size: 13px;
  color: #44506a;
  margin-bottom: 6px;
}

.field input {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid #d7deea;
  border-radius: 9px;
  font-size: 14px;
}

.field input:focus {
  outline: none;
  border-color: #4f80f0;
  box-shadow: 0 0 0 3px rgba(79, 128, 240, 0.15);
}

.login-error {
  margin: 4px 0 12px;
  font-size: 13px;
  color: #d33b3b;
}

.login-btn {
  width: 100%;
  padding: 11px;
  border: none;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b74e0, #5a8cf0);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
