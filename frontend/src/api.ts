let csrf = ''
export function setCSRF(value: string) { csrf = value }
export async function api(path: string, body?: unknown, method = 'GET') {
  const response = await fetch('/api/v1' + path, {
    method, credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', ...(csrf ? {'X-CSRF-Token': csrf} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  })
  const data = await response.json()
  if (!response.ok) {
    const error = new Error(data.error?.message || '请求失败') as Error & {status: number}
    error.status = response.status
    if (response.status === 401) window.dispatchEvent(new CustomEvent('session-expired'))
    throw error
  }
  return data.data
}
