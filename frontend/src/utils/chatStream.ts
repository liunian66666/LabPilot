export interface StreamEvent {
  type: 'status' | 'reset' | 'delta' | 'done' | 'error'
  text?: string
  data?: {
    reply: string
    session_id: string
    pending_approval_id?: string | null
    tools_used: string[]
    artifacts?: Array<{ type: string; id: string; title?: string; status?: string; content_hash?: string }>
  }
}

/** Decode UTF-8 and SSE frames across arbitrary network chunk boundaries. */
export async function readChatStream(response: Response, receive: (event: StreamEvent) => void) {
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  if (!response.body) throw new Error('浏览器不支持流式读取')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  try {
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) {
        const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as StreamEvent
        if (event.type === 'error') throw new Error(event.text || '回复生成中断')
        if (event.type === 'done') completed = true
        receive(event)
      }
      if (chunk.done) break
    }
    if (!completed) throw new Error('连接中断，回复可能不完整，请重试。')
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
