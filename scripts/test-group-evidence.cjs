const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../frontend/node_modules/typescript')
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/groupEvidence.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const context = { exports: {} }
vm.runInNewContext(compiled, context)
const { hasMatchedMembers, memberStatus, memberNames, groupEventLabel, groupClassificationNote, groupEvidenceText } = context.exports
const event = { resolution: 'unresolved', quantity: 1, snapshot: {
  member_evidence: 'snapshot_difference', member_candidates: [{ name: '测试客户', member_type: 2 }],
} }
assert.equal(hasMatchedMembers(event), true)
assert.equal(memberStatus(event), '已识别')
for (const [type, label] of [['group_left', '主动退群'], ['group_removed', '被移出群']]) {
  assert.equal(groupEventLabel({ ...event, event_type: 'group_change_unknown', snapshot: { ...event.snapshot, reported_event_type: type } }), label)
  assert.equal(groupEventLabel({ event_type: type, snapshot: {} }), label)
  assert.equal(groupEventLabel({ event_type: 'group_change_unknown', snapshot: { reported_event_type: type } }), label)
}
assert.equal(memberStatus({ ...event, quantity: 2 }), '成员待核实')
assert.equal(groupEventLabel({ event_type: 'group_change_unknown', snapshot: {} }), '群成员减少（退出方式未提供）')
assert.equal(memberNames(event), '测试客户')
assert.equal(hasMatchedMembers({ ...event, quantity: 2 }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: {} }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_evidence: 'snapshot_difference_inconclusive' } }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ member_id: 'known-id', member_type: 0 }] } }), true)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ member_id: 'staff', member_type: 1 }] } }), true)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{}] } }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, classification_reason: 'identity_unresolved' } }), false)
assert.equal(memberStatus({ ...event, snapshot: { ...event.snapshot, classification_reason: 'identity_unresolved' } }), '成员待核实')
assert.equal(hasMatchedMembers({ ...event, quantity: 2, snapshot: { ...event.snapshot, member_candidates: [{ member_id: 'duplicate' }, { member_id: ' duplicate ' }] } }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ member_id: '   ', name: '  ' }] } }), false)
assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ name: '  真实姓名  ' }] } }), true)
for (const name of ['未知', '未知客户', '姓名未提供', '姓名待补全', '成员待核实']) {
  assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ name: ` ${name} ` }] } }), false)
  assert.equal(hasMatchedMembers({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ member_id: 'valid-id', name }] } }), true)
}
assert.equal(memberStatus({ ...event, resolution: 'exact' }), '已识别')
assert.equal(memberNames({ ...event, snapshot: {} }), '此记录未保存可关联的成员标识或快照证据')
assert.equal(memberNames({ ...event, customer_id: 'external-id', snapshot: { customer_name: '未知客户' } }), 'external-id（姓名待补全）')
assert.equal(memberNames({ ...event, snapshot: { ...event.snapshot, member_candidates: [{ member_id: 'member-id' }] } }), 'member-id（姓名待补全）')
const splitExternal = {
  ...event, event_type: 'group_removed', resolution: 'inferred', customer_id: 'legacy-id',
  snapshot: { ...event.snapshot, reported_quantity: 3, counted_as_customer: true, customer_name: '旧字段姓名' },
}
assert.equal(hasMatchedMembers(splitExternal), true)
assert.equal(memberNames(splitExternal), '测试客户')
assert.equal(memberStatus(splitExternal), '已识别')
assert.equal(groupClassificationNote(splitExternal), '已计入外部客户被移出群统计。')
assert.equal(groupClassificationNote({ ...splitExternal, event_type: 'group_left' }), '已计入外部客户主动退群统计。')
assert.ok(groupEvidenceText(splitExternal).includes('系统推断'))
for (const [reason, text] of [
  ['internal_members', '企业内部员工'],
  ['member_type_unknown', '尚不能确定是否为外部客户'],
  ['identity_unresolved', '唯一关联到具体成员'],
  ['quit_reason_unknown', '退出原因'],
]) {
  const residual = { ...event, event_type: 'group_change_unknown', snapshot: { ...event.snapshot, classification_reason: reason } }
  assert.ok(groupClassificationNote(residual).includes(text))
  assert.ok(!groupClassificationNote(residual).includes('操作人'))
}
console.log('Group evidence checks passed: matched external/internal, missing/ambiguous evidence, count mismatch and exact identification.')
