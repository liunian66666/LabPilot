import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const exports = {}
const source = fs.readFileSync(new URL('../src/utils/chatStream.ts', import.meta.url), 'utf8')
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
  { exports, TextDecoder })
const { readChatStream } = exports
function response(text) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({ start(controller) {
    // Split every byte, including inside a Chinese character and a frame separator.
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte))
    controller.close()
  } }))
}
const received = []
await readChatStream(response(': heartbeat\n\ndata: {"type":"delta","text":"你好"}\r\n\r\ndata: {"type":"done","data":{"reply":"你好","session_id":"A","tools_used":[]}}\n\n'), event => received.push(event))
assert.equal(received[0].text, '你好')
assert.equal(received[1].type, 'done')
await assert.rejects(readChatStream(response('data: {"type":"delta","text":"部分"}\n\n'), () => {}), /连接中断/)
await assert.rejects(readChatStream(response('data: {"type":"error","text":"服务暂不可用"}\n\n'), () => {}), /服务暂不可用/)
console.log('SSE checks passed: split UTF-8, split frames, heartbeat, explicit errors, incomplete stream.')
