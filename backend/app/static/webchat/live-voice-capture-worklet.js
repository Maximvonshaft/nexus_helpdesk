'use strict';

var PROCESSOR_NAME = 'nexus-live-voice-capture-v1';
var DEFAULT_OUTPUT_SAMPLE_RATE = 16000;
var DEFAULT_FRAME_SAMPLES = 320;
var MAX_FRAME_SAMPLES = 2048;

class NexusLiveVoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    var processorOptions = options && options.processorOptions ? options.processorOptions : {};
    var requestedRate = Number(processorOptions.outputSampleRate || DEFAULT_OUTPUT_SAMPLE_RATE);
    var requestedFrameSamples = Number(processorOptions.frameSamples || DEFAULT_FRAME_SAMPLES);
    this.outputSampleRate = requestedRate === DEFAULT_OUTPUT_SAMPLE_RATE ? requestedRate : DEFAULT_OUTPUT_SAMPLE_RATE;
    var normalizedFrameSamples = Number.isFinite(requestedFrameSamples) ? Math.round(requestedFrameSamples) : DEFAULT_FRAME_SAMPLES;
    this.frameSamples = Math.max(1, Math.min(MAX_FRAME_SAMPLES, normalizedFrameSamples));
    this.inputPerOutput = Math.max(1, sampleRate / this.outputSampleRate);
    this.phase = 0;
    this.accumulator = 0;
    this.accumulatorCount = 0;
    this.frame = new Int16Array(this.frameSamples);
    this.frameOffset = 0;
    this.running = true;
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'stop') this.running = false;
    };
  }

  process(inputs, outputs) {
    var outputChannels = outputs[0] || [];
    for (var outputIndex = 0; outputIndex < outputChannels.length; outputIndex += 1) {
      outputChannels[outputIndex].fill(0);
    }
    if (!this.running) return false;
    var input = inputs[0] && inputs[0][0];
    if (!input || !input.length) return true;

    for (var index = 0; index < input.length; index += 1) {
      this.accumulator += input[index];
      this.accumulatorCount += 1;
      this.phase += 1;
      if (this.phase >= this.inputPerOutput) {
        this.phase -= this.inputPerOutput;
        var averaged = this.accumulatorCount ? this.accumulator / this.accumulatorCount : 0;
        this.pushSample(averaged);
        this.accumulator = 0;
        this.accumulatorCount = 0;
      }
    }
    return true;
  }

  pushSample(value) {
    var sample = Math.max(-1, Math.min(1, value));
    this.frame[this.frameOffset] = Math.round(sample < 0 ? sample * 0x8000 : sample * 0x7fff);
    this.frameOffset += 1;
    if (this.frameOffset < this.frame.length) return;
    var packet = this.frame.buffer;
    this.port.postMessage({ type: 'pcm16', buffer: packet }, [packet]);
    this.frame = new Int16Array(this.frameSamples);
    this.frameOffset = 0;
  }
}

registerProcessor(PROCESSOR_NAME, NexusLiveVoiceCaptureProcessor);
