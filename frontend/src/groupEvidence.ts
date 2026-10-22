type Row = Record<string, any>

// Snapshot comparison is an inference, distinct from an explicit per-member callback.
export function hasMatchedMembers(row: Row): boolean {
  const snapshot = row.snapshot || {}
  const members = snapshot.member_candidates
  return snapshot.member_evidence === 'snapshot_difference'
    && Array.isArray(members) && members.length > 0
    && members.length === row.quantity
    && members.every((m: Row) => m && (m.member_id || (m.name && !['未知', '未知客户'].includes(m.name))))
}

export function memberStatus(row: Row): string {
  return row.resolution === 'exact' || hasMatchedMembers(row) ? '已识别' : '成员待核实'
}

export function groupEventLabel(row: Row): string {
  const type = row.event_type === 'group_change_unknown' ? row.snapshot?.reported_event_type : row.event_type
  return ({ group_left: '主动退群', group_removed: '被移出群' } as Record<string, string>)[type]
    || '群成员减少（退出方式未提供）'
}

export function memberNames(row: Row): string {
  if (row.snapshot.customer_name && !['未知客户', '未知', '成员待核实'].includes(row.snapshot.customer_name)) return row.snapshot.customer_name
  if (row.customer_id) return `${row.customer_id}（姓名待补全）`
  if (hasMatchedMembers(row)) {
    return row.snapshot.member_candidates.map((m: Row) => m.name || (m.member_id ? `${m.member_id}（姓名待补全）` : '姓名未提供')).join('、')
  }
  return memberReason(row)
}

export function memberReason(row: Row): string {
  const reasons: Record<string, string> = {
    no_before_snapshot: '缺少事件发生前的群成员快照，无法比对离群成员',
    current_snapshot_unavailable: '事件后的群成员列表尚未获取成功，等待重试',
    snapshot_difference_inconclusive: '成员差异与回调人数不一致，或期间有成员加入，无法唯一关联',
  }
  return reasons[row.snapshot?.member_evidence] || '此记录未保存可关联的成员标识或快照证据'
}
