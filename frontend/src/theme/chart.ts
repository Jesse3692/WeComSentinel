export const eventTypes = ['contact_added', 'contact_deleted', 'group_left', 'group_removed']
const tokens: Record<string, string> = {contact_added:'--event-added',contact_deleted:'--event-deleted',group_left:'--event-left',group_removed:'--event-removed'}
export function themeColor(token: string) { return getComputedStyle(document.documentElement).getPropertyValue(token).trim() }
export function eventColor(type: string) { return themeColor(tokens[type] || '--text-secondary') }
export function chartColors() { return eventTypes.map(eventColor) }
