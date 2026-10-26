// 助手和详情可以同时持锁；最后一个浮层关闭后才恢复页面原有样式。
const locks = new Set<symbol>()
let originalOverflow = ''

export function lockPageScroll() {
  const key = Symbol('overlay')
  if (!locks.size) originalOverflow = document.body.style.overflow
  locks.add(key)
  document.body.style.overflow = 'hidden'
  return () => {
    if (!locks.delete(key)) return
    if (!locks.size) document.body.style.overflow = originalOverflow
  }
}
