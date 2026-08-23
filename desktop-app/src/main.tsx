import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/globals.css";

const root = document.getElementById("root")!;
if (new URLSearchParams(window.location.search).has("vad_eval")) {
  void import("./lib/vad-recorder")
    .then(({ runVadFixtureEval }) => runVadFixtureEval("/vad-eval/roundtable-speech.mp3"))
    .then((result) => {
      root.textContent = JSON.stringify(result);
      root.dataset.vadEval = result.passed ? "passed" : "failed";
    })
    .catch((error: unknown) => {
      root.textContent = JSON.stringify({
        passed: false,
        error: error instanceof Error ? error.message : String(error),
      });
      root.dataset.vadEval = "failed";
    });
} else {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
