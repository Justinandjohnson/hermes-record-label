import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = join(appRoot, "public", "vad");
await mkdir(outputDir, { recursive: true });

const vadDist = join(appRoot, "node_modules", "@ricky0123", "vad-web", "dist");
for (const name of ["vad.worklet.bundle.min.js", "silero_vad_v5.onnx"]) {
  await copyFile(join(vadDist, name), join(outputDir, name));
}

const ortDist = join(appRoot, "node_modules", "onnxruntime-web", "dist");
for (const name of ["ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"]) {
  await copyFile(join(ortDist, name), join(outputDir, name));
}
