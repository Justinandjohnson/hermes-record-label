import { useState, useEffect, useCallback } from "react";
import {
  getTracks,
  getProjects,
  getArtistProfile,
  getStats,
  getSessions,
  getExportEvents,
  loadSettings,
  saveSettings as bridgeSaveSettings,
} from "../lib/hermes-bridge";
import type {
  Track,
  Project,
  ArtistProfile,
  Stats,
  AbletonSession,
  ExportEvent,
  AppSettings,
} from "../lib/hermes-bridge";

export function useTracks() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setTracks(await getTracks());
    } catch (err) {
      console.error("Failed to fetch tracks:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { tracks, loading, refresh };
}

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setProjects(await getProjects());
    } catch (err) {
      console.error("Failed to fetch projects:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { projects, loading, refresh };
}

export function useArtistProfile() {
  const [profile, setProfile] = useState<ArtistProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getArtistProfile()
      .then(setProfile)
      .catch((err) => console.error("Failed to fetch profile:", err))
      .finally(() => setLoading(false));
  }, []);

  return { profile, loading };
}

export function useStats() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setStats(await getStats());
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { stats, loading, refresh };
}

export function useSessions() {
  const [sessions, setSessions] = useState<AbletonSession[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSessions(await getSessions());
    } catch (err) {
      console.error("Failed to fetch sessions:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { sessions, loading, refresh };
}

export function useExportEvents() {
  const [events, setEvents] = useState<ExportEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setEvents(await getExportEvents(50));
    } catch (err) {
      console.error("Failed to fetch export events:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { events, loading };
}

const DEFAULT_SETTINGS: AppSettings = {
  ableton_project_folder: "",
  ableton_export_folder: "",
  artist_name: "",
  artist_phone: "",
  quiet_hours_start: "22:00",
  quiet_hours_end: "09:00",
  quiet_days: [],
  dnd_enabled: false,
  remote_url: "",
  api_token: "",
};

export function useSettings() {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadSettings()
      .then(setSettings)
      .catch((err) => {
        console.error("Failed to load settings, using defaults:", err);
        setSettings(DEFAULT_SETTINGS);
      })
      .finally(() => setLoading(false));
  }, []);

  const saveSettings = useCallback(async (updated: AppSettings) => {
    await bridgeSaveSettings(updated);
    setSettings(updated);
  }, []);

  return { settings, saveSettings, loading };
}
