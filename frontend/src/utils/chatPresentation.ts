/** Keep internal object references in data, while showing one current report card. */
export function mergeReportCards<T extends { role: string; card?: { type: string; id: string } }>(rows: T[]): T[] {
  const result: T[] = []
  const reports = new Map<string, number>()
  for (const row of rows) {
    if (row.role === 'card' && (row.card?.type === 'report' || row.card?.type === 'approval')) {
      const key = `${row.card.type}:${row.card.id}`
      const index = reports.get(key)
      if (index !== undefined) {
        result[index] = row
        continue
      }
      reports.set(key, result.length)
    }
    result.push(row)
  }
  return result
}

export function displayChatText(text: string): string {
  const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
  return text
    .replace(new RegExp(`\\[active_(?:report|todo|approval)_id=${uuid}\\]\\s*`, 'gi'), '')
    .replace(new RegExp(`(?:report|todo|approval)_id=${uuid}`, 'gi'), '')
    .replace(/hash=[0-9a-f]{6,}/gi, '')
    .replace(/[（(]\s*[）)]/g, '')
    .trim()
}
