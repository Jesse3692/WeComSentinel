let csrf = ''
let generation = 0
export function setCSRF(value: string) { csrf = value }
export function sessionGeneration() { return generation }
export function invalidateSession() { generation++; csrf = '' }
export async function api(path: string, body?: unknown, method = 'GET', notifyExpired = true, signal?: AbortSignal) {
  const started = generation
  let response: Response
  try { response = await fetch('/api/v1' + path, {
    method, credentials: 'same-origin', signal,
    headers: {'Content-Type': 'application/json', ...(csrf ? {'X-CSRF-Token': csrf} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  }) } catch(error) {
    if(started !== generation) throw new DOMException('旧会话请求已忽略', 'AbortError')
    throw error
  }
  const data = await response.json()
  if(started !== generation) throw new DOMException('旧会话请求已忽略', 'AbortError')
  if (!response.ok) {
    const error = new Error(data.error?.message || '请求失败') as Error & {status: number}
    error.status = response.status
    if (response.status === 401 && notifyExpired) window.dispatchEvent(new CustomEvent('session-expired'))
    throw error
  }
  return data.data
}
