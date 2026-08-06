(function installScannerPcmRingPlayer() {
  if (window.ScannerPcmRingPlayer) return;

  class ScannerPcmRingPlayer {
    constructor(context, destination, options = {}) {
      this.context = context;
      this.inputRate = Number(options.inputRate) || 8000;
      this.startSamples = Math.max(160, Number(options.startSamples) || 640);
      this.maxSamples = Math.max(this.startSamples, Number(options.maxSamples) || 3600);
      this.buffer = new Int16Array(8192);
      this.readIndex = 0;
      this.writeIndex = 0;
      this.count = 0;
      this.phase = 0;
      this.started = false;
      this.droppedSamples = 0;
      this.underruns = 0;
      this.renderedSamples = 0;
      this.rateCorrection = 0;
      this.node = context.createScriptProcessor(2048, 0, 1);
      this.node.onaudioprocess = (event) => this.render(event.outputBuffer);
      this.node.connect(destination);
    }

    enqueue(samples) {
      for (let index = 0; index < samples.length; index += 1) {
        while (this.count >= this.maxSamples) {
          this.readIndex = (this.readIndex + 1) % this.buffer.length;
          this.count -= 1;
          this.droppedSamples += 1;
        }
        this.buffer[this.writeIndex] = samples[index];
        this.writeIndex = (this.writeIndex + 1) % this.buffer.length;
        this.count += 1;
      }
    }

    render(outputBuffer) {
      const channel = outputBuffer.getChannelData(0);
      channel.fill(0);
      if (!this.started) {
        if (this.count < this.startSamples) return;
        this.started = true;
      }

      const targetSamples = 960;
      const queueError = (this.count - targetSamples) / this.maxSamples;
      this.rateCorrection = Math.max(-0.01, Math.min(0.01, queueError * 0.02));
      const step = (this.inputRate / this.context.sampleRate) * (1 + this.rateCorrection);
      for (let index = 0; index < channel.length; index += 1) {
        if (this.count < 2) {
          this.started = false;
          this.underruns += 1;
          break;
        }
        const first = this.buffer[this.readIndex];
        const second = this.buffer[(this.readIndex + 1) % this.buffer.length];
        channel[index] = (first + ((second - first) * this.phase)) / 32768;
        this.phase += step;
        while (this.phase >= 1 && this.count > 1) {
          this.phase -= 1;
          this.readIndex = (this.readIndex + 1) % this.buffer.length;
          this.count -= 1;
        }
        this.renderedSamples += 1;
      }
    }

    reset() {
      this.readIndex = 0;
      this.writeIndex = 0;
      this.count = 0;
      this.phase = 0;
      this.started = false;
      this.droppedSamples = 0;
      this.underruns = 0;
      this.renderedSamples = 0;
      this.rateCorrection = 0;
    }

    diagnostics() {
      return {
        queuedSamples: this.count,
        queuedSeconds: this.count / this.inputRate,
        droppedSamples: this.droppedSamples,
        underruns: this.underruns,
        renderedSamples: this.renderedSamples,
        outputRate: this.context.sampleRate,
        rateCorrection: this.rateCorrection,
      };
    }
  }

  window.ScannerPcmRingPlayer = ScannerPcmRingPlayer;
})();
