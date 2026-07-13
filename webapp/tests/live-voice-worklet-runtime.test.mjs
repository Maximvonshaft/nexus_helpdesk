import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const source = fs.readFileSync(path.resolve(here, '../../backend/app/static/webchat/live-voice-capture-worklet.js'), 'utf8')

function loadProcessor(sampleRate = 48000) {
  let Processor = null
  class FakePort {
    constructor() {
      this.messages = []
      this.onmessage = null
    }
    postMessage(message, transfer) {
      this.messages.push({ message, transfer })
    }
  }
  class FakeAudioWorkletProcessor {
    constructor() {
      this.port = new FakePort()
    }
  }
  const context = vm.createContext({
    AudioWorkletProcessor: FakeAudioWorkletProcessor,
    Int16Array,
    Math,
    Number,
    sampleRate,
    registerProcessor(name, ctor) {
      assert.equal(name, 'nexus-live-voice-capture-v1')
      Processor = ctor
    },
  })
  vm.runInContext(source, context, { filename: 'live-voice-capture-worklet.js' })
  assert.ok(Processor)
  return Processor
}

function runUntilPacket(processor, inputSample = 0.5, inputLength = 960) {
  const input = new Float32Array(inputLength).fill(inputSample)
  const output = new Float32Array(128).fill(1)
  const keepRunning = processor.process([[input]], [[output]])
  return { keepRunning, output, messages: processor.port.messages }
}

test('worklet emits a 20ms 16kHz PCM16 transferable packet and silences output', () => {
  const Processor = loadProcessor(48000)
  const processor = new Processor({ processorOptions: { outputSampleRate: 16000, frameSamples: 320 } })
  const result = runUntilPacket(processor)

  assert.equal(result.keepRunning, true)
  assert.ok(result.output.every((value) => value === 0))
  assert.equal(result.messages.length, 1)
  const [{ message, transfer }] = result.messages
  assert.equal(message.type, 'pcm16')
  assert.equal(message.buffer.byteLength, 640)
  assert.equal(transfer.length, 1)
  assert.equal(transfer[0], message.buffer)
  const pcm = new Int16Array(message.buffer)
  assert.ok(pcm.every((value) => value === 16384))
})

test('requested frame size is bounded and invalid input falls back safely', () => {
  const Processor = loadProcessor(16000)
  const bounded = new Processor({ processorOptions: { frameSamples: 999999 } })
  runUntilPacket(bounded, 0.25, 2048)
  assert.equal(bounded.port.messages[0].message.buffer.byteLength, 4096)

  const fallback = new Processor({ processorOptions: { frameSamples: Number.NaN } })
  runUntilPacket(fallback, 0.25, 320)
  assert.equal(fallback.port.messages[0].message.buffer.byteLength, 640)
})

test('stop message terminates the processor without another packet', () => {
  const Processor = loadProcessor(48000)
  const processor = new Processor({ processorOptions: { frameSamples: 320 } })
  processor.port.onmessage({ data: { type: 'stop' } })
  const result = runUntilPacket(processor)
  assert.equal(result.keepRunning, false)
  assert.equal(result.messages.length, 0)
})
