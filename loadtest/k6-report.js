// k6 压测:报表并发场景(看板轮询 + 事件列表 + 选项搜索)。
//
// 前置条件(本地演示环境):
//   1. .env 中 DEMO_ENABLED=true 且设置 DEMO_PASSWORD(≥12 位)
//   2. uv run python -m app.cli seed-demo   # 演示数据
//   3. uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
//   4. 安装 k6: https://k6.io  (winget install k6 --source winget)
//
// 运行(密码用 .env 中的 DEMO_PASSWORD):
//   k6 run -e BASE_URL=http://127.0.0.1:8000 -e DEMO_PASSWORD=xxxx loadtest/k6-report.js
// 调整并发:
//   k6 run -e VUS=100 -e DURATION=5m loadtest/k6-report.js
// 对比缓存效果:先以 REPORT_CACHE_SECONDS=30 跑一遍,再设 0 重启后端跑一遍,
// 对比 dashboard 的 p95 与 RPS。
//
// 说明:演示登录限流为每 IP 每分钟 10 次,本脚本只在 setup 中登录一次并复用会话;
// 所有压测端点均为 GET,无需 CSRF 头。请勿对生产环境执行。

import http from 'k6/http'
import { check, sleep } from 'k6'

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000'
const DEMO_PASSWORD = __ENV.DEMO_PASSWORD || ''
const VUS = Number(__ENV.VUS || 50)
const DURATION = __ENV.DURATION || '3m'

export const options = {
  scenarios: {
    report_viewers: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: VUS },
        { duration: DURATION, target: VUS },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    'http_req_duration{endpoint:dashboard}': ['p(95)<500'],
    'http_req_duration{endpoint:events}': ['p(95)<800'],
    'http_req_duration{endpoint:employees}': ['p(95)<800'],
    'http_req_failed': ['rate<0.01'],
  },
}

export function setup() {
  if (!DEMO_PASSWORD) {
    throw new Error('请通过 -e DEMO_PASSWORD=... 提供演示密码(与后端 .env 一致)')
  }
  const response = http.post(`${BASE_URL}/api/v1/auth/demo`,
    JSON.stringify({ password: DEMO_PASSWORD, role: 'admin' }),
    { headers: { 'Content-Type': 'application/json' } })
  check(response, { 'login ok': (r) => r.status === 200 })
  if (response.status !== 200) {
    throw new Error(`演示登录失败: ${response.status} ${response.body}`)
  }
  const body = response.json()
  const cookie = response.cookies['monitor_session'][0].value
  return { cookie: `monitor_session=${cookie}` }
}

function get(session, path, endpoint) {
  return http.get(`${BASE_URL}${path}`, {
    headers: { Cookie: session.cookie },
    tags: { endpoint },
  })
}

export default function (session) {
  // 模拟前端 30s 轮询一次看板,其间浏览列表与搜索。
  get(session, '/api/v1/dashboard', 'dashboard')
  sleep(1)
  get(session, '/api/v1/events?limit=20', 'events')
  sleep(2)
  get(session, '/api/v1/employees?limit=50', 'employees')
  sleep(Math.random() * 3 + 1)
}
