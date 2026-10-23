"""Separate customer business categories from how group members were identified."""
from copy import deepcopy


def has_identity(member):
    if not isinstance(member, dict):
        return False
    identity = member.get("member_id")
    name = member.get("name")
    return (isinstance(identity, str) and bool(identity.strip())) or (
        isinstance(name, str) and bool(name.strip())
        and name.strip() not in {"未知", "未知客户", "姓名未提供", "姓名待补全", "成员待核实"})


def matched_members(snapshot, quantity):
    members = snapshot.get("_all_member_candidates", snapshot.get("member_candidates"))
    if (snapshot.get("member_evidence") != "snapshot_difference"
            or not isinstance(members, list) or not members or len(members) != quantity
            or not all(has_identity(member) for member in members)):
        return None
    ids = [m["member_id"].strip() for m in members
           if isinstance(m.get("member_id"), str) and m["member_id"].strip()]
    if len(set(ids)) != len(ids):
        return None
    return deepcopy(members)


def group_records(snapshot, quantity, occurred_at, group_id):
    """Count inferred external members in their business category, never internal staff.

    A mixed callback is partitioned into a customer record and a remainder. Stable
    item keys and the retained full evidence allow retries without double counting.
    """
    snapshot = deepcopy(snapshot)
    members = matched_members(snapshot, quantity)
    reason = snapshot.get("reported_event_type")
    external, other = [], []
    if members is not None:
        for member in members:
            member_type = str(member.get("member_type", ""))
            member["member_type"] = int(member_type) if member_type in ("1", "2") else None
            (external if member["member_type"] == 2 else other).append(member)
        snapshot["_all_member_candidates"] = deepcopy(members)
    else:
        snapshot.pop("_all_member_candidates", None)
    snapshot["reported_quantity"] = quantity
    # Remove the old blanket exclusion message. Classification-specific notes are
    # rendered by the client; inferred is intentionally distinct from exact.
    snapshot.pop("note", None)

    def record(key, selected, count, event_type, classification_reason):
        details = deepcopy(snapshot)
        if selected is not None:
            details["member_candidates"] = deepcopy(selected)
        details["classification_reason"] = classification_reason
        details["counted_as_customer"] = event_type in ("group_left", "group_removed")
        complete = bool(details.get("group_name") and selected and all(
            m.get("name") and m.get("member_type") in (1, 2) and m.get("avatar_state") != "retry" for m in selected))
        return {"item_key": key, "event_type": event_type, "occurred_at": occurred_at,
                "group_id": group_id, "quantity": count,
                "resolution": "inferred" if members is not None else "unresolved",
                "enrichment_status": "complete" if complete else "partial", "snapshot": details}

    if members is None:
        return [record("group", None, quantity, "group_change_unknown", "identity_unresolved")]
    if reason not in ("group_left", "group_removed"):
        return [record("group", members, quantity, "group_change_unknown", "quit_reason_unknown")]
    remainder_reason = ("internal_members" if all(m["member_type"] == 1 for m in other)
                        else "member_type_unknown")
    if not external:
        return [record("group", other, quantity, "group_change_unknown", remainder_reason)]
    records = [record("group", external, len(external), reason, "external_members")]
    if other:
        records.append(record("group_other", other, len(other), "group_change_unknown", remainder_reason))
    return records
