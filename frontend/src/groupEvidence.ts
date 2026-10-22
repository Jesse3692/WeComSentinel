type Row = Record<string, any>

// Snapshot comparison is an inference, distinct from an explicit per-member callback.
export function hasMatchedMembers(row: Row): boolean {
  const snapshot = row.snapshot || {}
  const members = snapshot.member_candidates
  if (snapshot.classification_reason === 'identity_unresolved'
      || snapshot.member_evidence !== 'snapshot_difference'
      || !Array.isArray(members) || members.length === 0 || members.length !== row.quantity) return false
  const placeholderNames = new Set(['未知', '未知客户', '姓名未提供', '姓名待补全', '成员待核实'])
  const ids = new Set<string>()
  return members.every((member: Row) => {
    if (!member || typeof member !== 'object' || Array.isArray(member)) return false
    const id = typeof member.member_id === 'string' ? member.member_id.trim() : ''
    const name = typeof member.name === 'string' ? member.name.trim() : ''
    if (!id && (!name || placeholderNames.has(name))) return false
    if (id && ids.has(id)) return false
    if (id) ids.add(id)
    return true
  })
}

export function memberStatus(row: Row): string {
  return row.resolution === 'exact' || hasMatchedMembers(row) ? '已识别' : '成员待核实'
}

export function groupEventLabel(row: Row): string {
  const type = row.event_type === 'group_change_unknown' ? row.snapshot?.reported_event_type : row.event_type
  return ({ group_left: '主动退群', group_removed: '被移出群' } as Record<string, string>)[type]
    || '群成员减少（退出方式未提供）'
}

export function groupClassificationNote(row: Row): string {
  if (['group_left', 'group_removed'].includes(row.event_type)) {
    return `已计入外部客户${row.event_type === 'group_left' ? '主动退群' : '被移出群'}统计。`
  }
  const reasons: Record<string, string> = {
    internal_members: '本条涉及企业内部员工，保留操作记录，不计入外部客户流失统计。',
    member_type_unknown: '成员已关联，但尚不能确定是否为外部客户，暂不计入客户流失统计。',
    identity_unresolved: '尚不能将平台报告的变更唯一关联到具体成员，暂不计入客户流失统计。',
    quit_reason_unknown: '平台未提供可识别的退出原因，暂不能归为主动退群或被移出群。',
  }
  return reasons[row.snapshot?.classification_reason] || '本条仍缺少客户身份或退出原因的完整证据，暂不计入客户流失统计。'
}

export function groupEvidenceText(row: Row): string {
  if (hasMatchedMembers(row)) {
    return '根据变更前后成员列表比对关联；平台报告群成员变更，成员关联为系统推断。'
  }
  return row.resolution === 'exact' ? '记录中已保存明确的成员身份。' : memberReason(row)
}

export function memberNames(row: Row): string {
  if (row.resolution === 'inferred' && hasMatchedMembers(row)) {
    return row.snapshot.member_candidates.map((m: Row) => m.name || (m.member_id ? `${m.member_id}（姓名待补全）` : '姓名未提供')).join('、')
  }
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
