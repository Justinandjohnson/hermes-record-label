import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_TARGET = "http://localhost:8086";

const API_ROUTES = [
  "/api",
  "/tracks",
  "/feedback",
  "/projects",
  "/artist_profile",
  "/release_states",
  "/stats",
  "/sessions",
  "/export_events",
  "/track_audio",
  "/artist_message",
  "/settings",
  "/artwork",
  "/audio_features",
  "/event",
  "/health",
  "/insights",
  "/segments",
  "/stt",
  "/tts",
  "/token",
  "/verdict",
  "/wave_vault",
];

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
    proxy: Object.fromEntries(
      API_ROUTES.map((route) => [route, { target: API_TARGET, changeOrigin: true }]),
    ),
  },
});
