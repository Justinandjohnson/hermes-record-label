import { useState, useEffect } from "react";
import { listen } from "@tauri-apps/api/event";

interface FileEvent {
  path: string;
  kind: "created" | "modified" | "removed";
}

export function useFileWatcher() {
  const [lastEvent, setLastEvent] = useState<FileEvent | null>(null);
  const [watching, setWatching] = useState(false);

  useEffect(() => {
    const unlisten = listen<FileEvent>("file-watcher://event", (event) => {
      setLastEvent(event.payload);
    });

    const unlistenStatus = listen<boolean>("file-watcher://status", (event) => {
      setWatching(event.payload);
    });

    return () => {
      unlisten.then((fn) => fn());
      unlistenStatus.then((fn) => fn());
    };
  }, []);

  return { lastEvent, watching };
}
