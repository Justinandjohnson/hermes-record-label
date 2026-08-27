import { useEffect, useState } from "react";
import { getTrackAudioElement } from "./track-playback";
import { getVoiceAudioElement } from "./voice-playback";

export interface AudioLevels {
  energy: number; // 0.0 to 1.0
  bands: number[]; // Normalized band values 0.0 to 1.0
}

let audioCtx: AudioContext | null = null;
let trackSource: MediaElementAudioSourceNode | null = null;
let trackAnalyser: AnalyserNode | null = null;
let voiceSource: MediaElementAudioSourceNode | null = null;
let voiceAnalyser: AnalyserNode | null = null;

function ensureAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (AudioContextClass) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx && audioCtx.state === "suspended") {
    void audioCtx.resume();
  }
  return audioCtx;
}

export function setupTrackAnalyser(): AnalyserNode | null {
  const ctx = ensureAudioContext();
  if (!ctx) return null;
  if (!trackAnalyser) {
    try {
      const el = getTrackAudioElement();
      trackSource = ctx.createMediaElementSource(el);
      trackAnalyser = ctx.createAnalyser();
      trackAnalyser.fftSize = 64;
      trackAnalyser.smoothingTimeConstant = 0.8;
      trackSource.connect(trackAnalyser);
      trackAnalyser.connect(ctx.destination);
    } catch {
      // Element might already be connected or not ready
    }
  }
  return trackAnalyser;
}

export function setupVoiceAnalyser(): AnalyserNode | null {
  const ctx = ensureAudioContext();
  if (!ctx) return null;
  if (!voiceAnalyser) {
    try {
      const el = getVoiceAudioElement();
      voiceSource = ctx.createMediaElementSource(el);
      voiceAnalyser = ctx.createAnalyser();
      voiceAnalyser.fftSize = 32;
      voiceAnalyser.smoothingTimeConstant = 0.75;
      voiceSource.connect(voiceAnalyser);
      voiceAnalyser.connect(ctx.destination);
    } catch {
      // Element might already be connected or not ready
    }
  }
  return voiceAnalyser;
}

type LevelListener = (levels: AudioLevels) => void;

const trackListeners = new Set<LevelListener>();
const voiceListeners = new Set<LevelListener>();

let trackRafId: number | null = null;
let voiceRafId: number | null = null;

function runTrackLoop() {
  if (trackListeners.size === 0) {
    trackRafId = null;
    return;
  }
  const analyser = setupTrackAnalyser();
  if (analyser) {
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteFrequencyData(dataArray);

    let sum = 0;
    const bands: number[] = [];
    const step = Math.max(1, Math.floor(bufferLength / 16));
    for (let i = 0; i < bufferLength && bands.length < 16; i += step) {
      const val = dataArray[i] / 255;
      bands.push(val);
      sum += val;
    }
    const energy = Math.min(1, (sum / Math.max(1, bands.length)) * 1.6);
    const levels: AudioLevels = { energy, bands };
    for (const listener of trackListeners) {
      listener(levels);
    }
  }
  trackRafId = requestAnimationFrame(runTrackLoop);
}

function runVoiceLoop() {
  if (voiceListeners.size === 0) {
    voiceRafId = null;
    return;
  }
  const analyser = setupVoiceAnalyser();
  if (analyser) {
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteFrequencyData(dataArray);

    let sum = 0;
    const bands: number[] = [];
    const step = Math.max(1, Math.floor(bufferLength / 6));
    for (let i = 0; i < bufferLength && bands.length < 6; i += step) {
      const val = dataArray[i] / 255;
      bands.push(val);
      sum += val;
    }
    const energy = Math.min(1, (sum / Math.max(1, bands.length)) * 2.0);
    const levels: AudioLevels = { energy, bands };
    for (const listener of voiceListeners) {
      listener(levels);
    }
  }
  voiceRafId = requestAnimationFrame(runVoiceLoop);
}

export function subscribeTrackLevels(listener: LevelListener): () => void {
  trackListeners.add(listener);
  ensureAudioContext();
  if (trackRafId === null) {
    trackRafId = requestAnimationFrame(runTrackLoop);
  }
  return () => {
    trackListeners.delete(listener);
  };
}

export function subscribeVoiceLevels(listener: LevelListener): () => void {
  voiceListeners.add(listener);
  ensureAudioContext();
  if (voiceRafId === null) {
    voiceRafId = requestAnimationFrame(runVoiceLoop);
  }
  return () => {
    voiceListeners.delete(listener);
  };
}

export function useTrackLevels(active: boolean): AudioLevels {
  const [levels, setLevels] = useState<AudioLevels>({ energy: 0, bands: [] });
  useEffect(() => {
    if (!active) {
      setLevels({ energy: 0, bands: [] });
      return;
    }
    return subscribeTrackLevels(setLevels);
  }, [active]);
  return levels;
}

export function useVoiceLevels(active: boolean): AudioLevels {
  const [levels, setLevels] = useState<AudioLevels>({ energy: 0, bands: [] });
  useEffect(() => {
    if (!active) {
      setLevels({ energy: 0, bands: [] });
      return;
    }
    return subscribeVoiceLevels(setLevels);
  }, [active]);
  return levels;
}
