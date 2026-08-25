/** Neural, browser-side voice activity detection for one hands-free turn. */
import type { MicVAD } from "@ricky0123/vad-web";

export interface VadOptions {
  deviceId?: string;
  /** Live input gain; read for every audio frame so sliders apply immediately. */
  inputGain?: () => number;
  onSpeechStart?: () => void;
  onReady?: () => void;
  onLevel?: (level: number) => void;
  onDiagnostics?: (diagnostics: VadDiagnostics) => void;
}

export interface VadDiagnostics {
  deviceId: string;
  deviceLabel: string;
  availableInputs: Array<{ deviceId: string; label: string }>;
  frameCount: number;
  rawRms: number;
  neuralSpeech: number;
  trackMuted: boolean;
  trackState: MediaStreamTrackState;
}

export interface VadSession {
  /** Resolves with a mono 16 kHz PCM WAV after 4.5 seconds of silence. */
  promise: Promise<Blob>;
  /** Stops the VAD and releases its microphone/model resources. */
  cancel: () => void;
}

interface ActiveTurn {
  opts: VadOptions;
  resolve: (blob: Blob) => void;
  reject: (error: Error) => void;
  settled: boolean;
  speaking: boolean;
  speechFrames: number;
  silenceFrames: number;
  preRoll: Float32Array[];
  recording: Float32Array[];
  calibration: number[];
  /** Cached sorted copy of calibration — recomputed only when calibration changes. */
  calibrationSorted: number[];
  calibrationDirty: boolean;
  /** performance.now() of the last onLevel emission (throttle for React renders). */
  lastLevelEmitAt: number;
  peakRms: number;
  onComplete: () => void;
  deviceLabel: string;
  deviceId: string;
  availableInputs: Array<{ deviceId: string; label: string }>;
  frameCount: number;
  trackMuted: boolean;
  trackState: MediaStreamTrackState;
}

const SAMPLE_RATE = 16_000;
const END_OF_TURN_SILENCE_MS = 4_500;
const FRAME_MS = 32; // Silero v5 emits 512 samples at 16 kHz.
const PRE_ROLL_FRAMES = Math.ceil(800 / FRAME_MS);
const END_SILENCE_FRAMES = Math.ceil(END_OF_TURN_SILENCE_MS / FRAME_MS);
/** onLevel drives React state; ~11 Hz is smooth under the 100ms CSS bar transition. */
const LEVEL_EMIT_INTERVAL_MS = 90;
let enginePromise: Promise<MicVAD> | null = null;
let activeTurn: ActiveTurn | null = null;
const VIRTUAL_INPUT = /droidcam|stereo mix|virtual|vb-audio|cable/i;

function calibrationPercentile(turn: ActiveTurn): number {
  if (turn.calibrationDirty) {
    turn.calibrationSorted = [...turn.calibration].sort((a, b) => a - b);
    turn.calibrationDirty = false;
  }
  const sorted = turn.calibrationSorted;
  return sorted.length >= 5 ? sorted[Math.floor(sorted.length * 0.25)] ?? 0 : 0;
}

function applyTrackDiagnostics(stream: MediaStream): void {
  const track = stream.getAudioTracks()[0];
  if (track && activeTurn) {
    activeTurn.deviceLabel = track.label || "Default microphone";
    activeTurn.deviceId = track.getSettings().deviceId || "default";
    activeTurn.trackMuted = track.muted;
    activeTurn.trackState = track.readyState;
  }
}

async function openMicrophoneStream(): Promise<MediaStream> {
  const constraints: MediaTrackConstraints = {
    channelCount: 1,
    echoCancellation: true,
    autoGainControl: true,
    noiseSuppression: true,
  };
  const requestedDeviceId = activeTurn?.opts.deviceId;
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: requestedDeviceId
        ? { ...constraints, deviceId: { exact: requestedDeviceId } }
        : constraints,
    });
  } catch (error) {
    if (!requestedDeviceId) throw error;
    // A saved browser device ID can change after an interface reconnects.
    stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
  }
  const selected = stream.getAudioTracks()[0];
  const devices = await navigator.mediaDevices.enumerateDevices();
  if (activeTurn) {
    activeTurn.availableInputs = devices
      .filter((device) => device.kind === "audioinput" && Boolean(device.deviceId))
      .map((device) => ({ deviceId: device.deviceId, label: device.label || "Microphone" }));
  }
  if (!requestedDeviceId && selected && VIRTUAL_INPUT.test(selected.label)) {
    const physicalInput = devices.find(
      (device) =>
        device.kind === "audioinput" &&
        Boolean(device.deviceId) &&
        Boolean(device.label) &&
        !VIRTUAL_INPUT.test(device.label),
    );
    if (physicalInput) {
      stream.getTracks().forEach((track) => track.stop());
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { ...constraints, deviceId: { exact: physicalInput.deviceId } },
      });
    }
  }
  applyTrackDiagnostics(stream);
  return stream;
}

function encodePcm16Wav(samples: Float32Array): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
  };

  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function joinFrames(frames: Float32Array[]): Float32Array {
  const size = frames.reduce((total, frame) => total + frame.length, 0);
  const audio = new Float32Array(size);
  let offset = 0;
  for (const frame of frames) {
    audio.set(frame, offset);
    offset += frame.length;
  }
  return audio;
}

function frameRms(frame: Float32Array): number {
  let squares = 0;
  for (const sample of frame) squares += sample * sample;
  return Math.sqrt(squares / frame.length);
}

function processTurnFrame(turn: ActiveTurn | null, isSpeech: number, frame: Float32Array): void {
  if (!turn || turn.settled) return;
  const gain = Math.max(0.25, Math.min(4, turn.opts.inputGain?.() ?? 1));
  const inputFrame = gain === 1
    ? frame
    : Float32Array.from(frame, (sample) => Math.max(-1, Math.min(1, sample * gain)));
  const rms = frameRms(inputFrame);
  turn.frameCount += 1;
  if (turn.frameCount % 8 === 0) {
    turn.opts.onDiagnostics?.({
      deviceId: turn.deviceId,
      deviceLabel: turn.deviceLabel,
      availableInputs: turn.availableInputs,
      frameCount: turn.frameCount,
      rawRms: rms,
      neuralSpeech: isSpeech,
      trackMuted: turn.trackMuted,
      trackState: turn.trackState,
    });
  }

  if (!turn.speaking && turn.calibration.length < 80) {
    turn.calibration.push(rms);
    turn.calibrationDirty = true;
  }
  const noiseFloor = calibrationPercentile(turn);
  const energyStart = Math.max(0.0025, noiseFloor * 2.75);
  // Silero remains the primary signal. Energy is a fallback for quiet voices
  // and microphones whose browser processing suppresses the model score.
  const startsSpeech = isSpeech >= 0.3 || rms >= energyStart;
  const level = Math.min(1, Math.max(isSpeech, rms / Math.max(energyStart, 0.0025)));
  const now = performance.now();
  if (now - turn.lastLevelEmitAt >= LEVEL_EMIT_INTERVAL_MS) {
    turn.lastLevelEmitAt = now;
    turn.opts.onLevel?.(level);
  }

  if (!turn.speaking) {
    turn.preRoll.push(inputFrame.slice());
    if (turn.preRoll.length > PRE_ROLL_FRAMES) turn.preRoll.shift();
    turn.speechFrames = startsSpeech ? turn.speechFrames + 1 : 0;
    if (turn.speechFrames >= 3) {
      turn.speaking = true;
      turn.peakRms = rms;
      turn.recording = [...turn.preRoll];
      turn.opts.onSpeechStart?.();
    }
    return;
  }

  turn.recording.push(inputFrame.slice());
  turn.peakRms = Math.max(rms, turn.peakRms * 0.995);
  // Once speech has begun, low-energy/non-neural frames teach the release
  // gate the interface's actual hiss/static floor. This prevents a fixed
  // floor from keeping the turn open forever without clipping quiet words.
  if (isSpeech < 0.15 && rms < Math.max(0.001, turn.peakRms * 0.6)) {
    turn.calibration.push(rms);
    turn.calibrationDirty = true;
    if (turn.calibration.length > 160) turn.calibration.shift();
  }
  const releaseFloor = calibrationPercentile(turn);
  const energyContinue = Math.max(0.0008, releaseFloor * 1.35, turn.peakRms * 0.1);
  const continuesSpeech = isSpeech >= 0.2 || rms >= energyContinue;
  turn.silenceFrames = continuesSpeech ? 0 : turn.silenceFrames + 1;
  if (turn.silenceFrames < END_SILENCE_FRAMES) return;

  turn.settled = true;
  turn.resolve(encodePcm16Wav(joinFrames(turn.recording)));
  turn.onComplete();
}

async function getEngine(): Promise<MicVAD> {
  if (!enginePromise) {
    enginePromise = import("@ricky0123/vad-web").then(({ MicVAD: MicVadClass }) =>
      MicVadClass.new({
          model: "v5",
          baseAssetPath: "/vad/",
          onnxWASMBasePath: "/vad/",
          startOnLoad: false,
          processorType: "auto",
          positiveSpeechThreshold: 0.3,
          negativeSpeechThreshold: 0.2,
          redemptionMs: END_OF_TURN_SILENCE_MS,
          preSpeechPadMs: 800,
          minSpeechMs: 300,
          submitUserSpeechOnPause: false,
          getStream: openMicrophoneStream,
          resumeStream: openMicrophoneStream,
          onFrameProcessed: (probabilities, frame) =>
            processTurnFrame(activeTurn, probabilities.isSpeech, frame),
          onSpeechStart: () => undefined,
          onSpeechRealStart: () => undefined,
          onVADMisfire: () => activeTurn?.opts.onLevel?.(0),
          onSpeechEnd: () => undefined,
        }),
    );
  }
  return enginePromise;
}

export function startVadListening(opts: VadOptions = {}): VadSession {
  let cancelled = false;
  let turn: ActiveTurn;
  const promise = new Promise<Blob>((resolve, reject) => {
    turn = {
      opts,
      resolve,
      reject,
      settled: false,
      speaking: false,
      speechFrames: 0,
      silenceFrames: 0,
      preRoll: [],
      recording: [],
      calibration: [],
      calibrationSorted: [],
      calibrationDirty: false,
      lastLevelEmitAt: 0,
      peakRms: 0,
      onComplete: () => {
        if (activeTurn === turn) activeTurn = null;
        void enginePromise?.then((engine) => engine.pause()).catch(() => undefined);
      },
      deviceLabel: "Default microphone",
      deviceId: "default",
      availableInputs: [],
      frameCount: 0,
      trackMuted: false,
      trackState: "live",
    };
    void getEngine()
      .then(async (engine) => {
        if (cancelled) return;
        if (activeTurn && !activeTurn.settled) {
          activeTurn.settled = true;
          activeTurn.reject(new Error("Listening cancelled"));
        }
        activeTurn = turn;
        await engine.start();
        if (!cancelled) opts.onReady?.();
      })
      .catch((error: unknown) => {
        if (cancelled || turn.settled) return;
        turn.settled = true;
        if (activeTurn === turn) activeTurn = null;
        reject(error instanceof Error ? error : new Error("Microphone setup failed"));
      });
  });

  return {
    promise,
    cancel: () => {
      if (cancelled || turn.settled) return;
      cancelled = true;
      turn.settled = true;
      if (activeTurn === turn) activeTurn = null;
      turn.reject(new Error("Listening cancelled"));
      void enginePromise?.then((engine) => engine.pause()).catch(() => undefined);
    },
  };
}

export interface VadFixtureEvalResult {
  passed: boolean;
  speechStarted: boolean;
  energyFallbackStarted: boolean;
  outputSeconds: number;
  energyFallbackOutputSeconds: number;
  silenceAfterPlaybackMs: number;
  wavType: string;
  wavBytes: number;
}

/**
 * Browser eval: feeds a real spoken clip through the same model, AudioWorklet,
 * thresholds, frame collector, and 4.5-second end-of-turn rule as Live Mode.
 */
export async function runVadFixtureEval(audioUrl: string): Promise<VadFixtureEvalResult> {
  const { MicVAD: MicVadClass } = await import("@ricky0123/vad-web");
  const context = new AudioContext();
  const encoded = await fetch(audioUrl).then((response) => {
    if (!response.ok) throw new Error(`Speech fixture failed to load: HTTP ${response.status}`);
    return response.arrayBuffer();
  });
  const decoded = await context.decodeAudioData(encoded);
  const destination = context.createMediaStreamDestination();
  const source = context.createBufferSource();
  source.buffer = decoded;
  source.connect(destination);
  // Reproduce a real interface noise floor that remains after speech ends.
  // 60 Hz at this level is below onset, but used to keep the old release gate open.
  const noise = context.createOscillator();
  const noiseGain = context.createGain();
  noise.frequency.value = 60;
  noiseGain.gain.value = 0.003;
  noise.connect(noiseGain).connect(destination);

  let speechStarted = false;
  let energyFallbackStarted = false;
  let playbackEndedAt = 0;
  let completedAt = 0;
  source.onended = () => {
    playbackEndedAt = performance.now();
  };

  let turn: ActiveTurn;
  const blobPromise = new Promise<Blob>((resolve, reject) => {
    turn = {
      opts: { onSpeechStart: () => { speechStarted = true; } },
      resolve,
      reject,
      settled: false,
      speaking: false,
      speechFrames: 0,
      silenceFrames: 0,
      preRoll: [],
      recording: [],
      calibration: [],
      calibrationSorted: [],
      calibrationDirty: false,
      lastLevelEmitAt: 0,
      peakRms: 0,
      onComplete: () => { completedAt = performance.now(); },
      deviceLabel: "Fixture stream",
      deviceId: "fixture",
      availableInputs: [],
      frameCount: 0,
      trackMuted: false,
      trackState: "live",
    };
  });
  let energyTurn: ActiveTurn;
  const energyBlobPromise = new Promise<Blob>((resolve, reject) => {
    energyTurn = {
      opts: { onSpeechStart: () => { energyFallbackStarted = true; } },
      resolve,
      reject,
      settled: false,
      speaking: false,
      speechFrames: 0,
      silenceFrames: 0,
      preRoll: [],
      recording: [],
      calibration: [],
      calibrationSorted: [],
      calibrationDirty: false,
      lastLevelEmitAt: 0,
      peakRms: 0,
      onComplete: () => undefined,
      deviceLabel: "Fixture stream",
      deviceId: "fixture",
      availableInputs: [],
      frameCount: 0,
      trackMuted: false,
      trackState: "live",
    };
  });

  const vad = await MicVadClass.new({
    model: "v5",
    baseAssetPath: "/vad/",
    onnxWASMBasePath: "/vad/",
    startOnLoad: false,
    processorType: "AudioWorklet",
    positiveSpeechThreshold: 0.3,
    negativeSpeechThreshold: 0.2,
    redemptionMs: END_OF_TURN_SILENCE_MS,
    preSpeechPadMs: 800,
    minSpeechMs: 300,
    submitUserSpeechOnPause: false,
    getStream: async () => destination.stream,
    pauseStream: async () => undefined,
    resumeStream: async () => destination.stream,
    onFrameProcessed: (probabilities, frame) => {
      processTurnFrame(turn, probabilities.isSpeech, frame);
      processTurnFrame(energyTurn, 0, frame);
    },
    onSpeechStart: () => undefined,
    onSpeechRealStart: () => undefined,
    onVADMisfire: () => undefined,
    onSpeechEnd: () => undefined,
  });

  try {
    await vad.start();
    noise.start();
    source.start();
    const [blob, energyBlob] = await Promise.race([
      Promise.all([blobPromise, energyBlobPromise]),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("VAD fixture did not close the speech turn")), 16_000),
      ),
    ]);
    const outputSeconds = (blob.size - 44) / 2 / SAMPLE_RATE;
    const energyFallbackOutputSeconds = (energyBlob.size - 44) / 2 / SAMPLE_RATE;
    const silenceAfterPlaybackMs = completedAt - playbackEndedAt;
    return {
      passed:
        speechStarted &&
        energyFallbackStarted &&
        outputSeconds >= 2 &&
        energyFallbackOutputSeconds >= 2 &&
        silenceAfterPlaybackMs >= 3_500 &&
        silenceAfterPlaybackMs <= 6_000 &&
        blob.type === "audio/wav",
      speechStarted,
      energyFallbackStarted,
      outputSeconds: Number(outputSeconds.toFixed(2)),
      energyFallbackOutputSeconds: Number(energyFallbackOutputSeconds.toFixed(2)),
      silenceAfterPlaybackMs: Math.round(silenceAfterPlaybackMs),
      wavType: blob.type,
      wavBytes: blob.size,
    };
  } finally {
    noise.stop();
    await vad.destroy().catch(() => undefined);
    await context.close().catch(() => undefined);
  }
}
