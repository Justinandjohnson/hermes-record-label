"""Parse Ableton Live Set (.als) project files.

.als files are gzip-compressed XML. This module decompresses and extracts
tempo, time signature, musical key, track names, and plugin names.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class ALSInfo:
    """Extracted metadata from an Ableton Live Set."""

    bpm: float | None = None
    time_sig_num: int | None = None
    time_sig_den: int | None = None
    musical_key: str | None = None
    ableton_version: str | None = None
    track_names: list[str] = field(default_factory=list)
    clip_names: list[str] = field(default_factory=list)
    plugin_names: list[str] = field(default_factory=list)
    als_hash: str | None = None


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _hash_file(path: Path) -> str | None:
    """MD5 hash of the raw .als bytes on disk."""
    try:
        h = hashlib.md5()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        logger.exception("Failed hashing %s", path)
        return None


def _value_attr(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    return elem.get("Value")


def _find_first_value(root: ET.Element, tag: str) -> str | None:
    """Find first descendant *tag* element and return its Value attr."""
    for el in root.iter(tag):
        v = el.get("Value")
        if v is not None:
            return v
    return None


def _extract_bpm(root: ET.Element) -> float | None:
    """Tempo lives in MasterTrack > DeviceChain > Mixer > Tempo.

    Inside Tempo there's either ManualValue or a value child element. We
    look for any Tempo element and then probe its descendants.
    """
    for tempo in root.iter("Tempo"):
        # Direct Manual / ManualValue child
        for tag in ("Manual", "ManualValue", "CurrentValue"):
            for child in tempo.iter(tag):
                v = child.get("Value")
                if v is not None:
                    try:
                        return float(v)
                    except ValueError:
                        continue
        # AutomationTarget siblings — look anywhere inside the Tempo subtree.
        for child in tempo.iter():
            v = child.get("Value")
            if v is None:
                continue
            try:
                fv = float(v)
            except ValueError:
                continue
            # Sanity bounds: real-world BPMs are roughly 20 - 999.
            if 20.0 <= fv <= 999.0:
                return fv
    return None


def _extract_time_sig(root: ET.Element) -> tuple[int | None, int | None]:
    num: int | None = None
    den: int | None = None
    n_val = _find_first_value(root, "TimeSignatureNumerator")
    d_val = _find_first_value(root, "TimeSignatureDenominator")
    try:
        if n_val is not None:
            num = int(n_val)
    except ValueError:
        pass
    try:
        if d_val is not None:
            den = int(d_val)
    except ValueError:
        pass
    return num, den


def _extract_musical_key(root: ET.Element) -> str | None:
    """Read ScaleInformation -> RootNote + Name."""
    for scale in root.iter("ScaleInformation"):
        root_note: str | None = None
        scale_name: str | None = None
        for rn in scale.iter("RootNote"):
            v = rn.get("Value")
            if v is not None:
                try:
                    idx = int(v) % 12
                    root_note = _NOTE_NAMES[idx]
                except ValueError:
                    root_note = v
                break
        for nm in scale.iter("Name"):
            v = nm.get("Value")
            if v is not None:
                scale_name = v
                break
        if root_note and scale_name:
            return f"{root_note} {scale_name}"
        if root_note:
            return root_note
    return None


def _extract_track_names(root: ET.Element) -> list[str]:
    names: list[str] = []
    for track_tag in ("AudioTrack", "MidiTrack", "ReturnTrack", "GroupTrack"):
        for track in root.iter(track_tag):
            for name in track.iter("Name"):
                user_name = name.find("UserName")
                effective = name.find("EffectiveName")
                value: str | None = None
                if user_name is not None and user_name.get("Value"):
                    value = user_name.get("Value")
                elif effective is not None and effective.get("Value"):
                    value = effective.get("Value")
                if value:
                    names.append(value)
                break
    return names


def _extract_clip_names(root: ET.Element) -> list[str]:
    names: list[str] = []
    for clip_tag in ("AudioClip", "MidiClip"):
        for clip in root.iter(clip_tag):
            n = clip.find("Name")
            v = _value_attr(n)
            if v:
                names.append(v)
    return names


def _extract_plugin_names(root: ET.Element) -> list[str]:
    names: list[str] = []
    # VST3 / VST plugins
    for tag in ("VstPluginInfo", "Vst3PluginInfo"):
        for info in root.iter(tag):
            for plug in info.iter("PlugName"):
                v = plug.get("Value")
                if v:
                    names.append(v)
    # AU plugins
    for info in root.iter("AuPluginInfo"):
        for name in info.iter("Name"):
            v = name.get("Value")
            if v:
                names.append(v)
        # AU also uses ManufacturerName + plugin Name in some versions
        for name in info.iter("PlugName"):
            v = name.get("Value")
            if v:
                names.append(v)
    return names


def parse_als(path: Path) -> ALSInfo:
    """Parse an .als file. Returns partial ALSInfo on any failure."""
    info = ALSInfo()
    path = Path(path)
    info.als_hash = _hash_file(path)

    try:
        with gzip.open(path, "rb") as fh:
            data = fh.read()
    except (OSError, EOFError):
        logger.exception("Failed to decompress %s", path)
        return info

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        logger.exception("Failed to parse XML in %s", path)
        return info

    info.ableton_version = root.get("Creator") or root.get("MinorVersion")

    try:
        info.bpm = _extract_bpm(root)
    except Exception:
        logger.exception("BPM extraction failed for %s", path)

    try:
        info.time_sig_num, info.time_sig_den = _extract_time_sig(root)
    except Exception:
        logger.exception("Time signature extraction failed for %s", path)

    try:
        info.musical_key = _extract_musical_key(root)
    except Exception:
        logger.exception("Key extraction failed for %s", path)

    try:
        info.track_names = _extract_track_names(root)
    except Exception:
        logger.exception("Track names extraction failed for %s", path)

    try:
        info.clip_names = _extract_clip_names(root)
    except Exception:
        logger.exception("Clip names extraction failed for %s", path)

    try:
        info.plugin_names = _extract_plugin_names(root)
    except Exception:
        logger.exception("Plugin names extraction failed for %s", path)

    return info


def diff_als(prev: ALSInfo | None, curr: ALSInfo) -> dict[str, Any]:
    """Compute a coarse-grained diff between two parsed .als snapshots."""
    if prev is None:
        return {
            "added_tracks": list(curr.track_names),
            "removed_tracks": [],
            "bpm_changed": curr.bpm is not None,
            "new_plugins": list(curr.plugin_names),
            "key_changed": curr.musical_key is not None,
        }

    prev_tracks = set(prev.track_names)
    curr_tracks = set(curr.track_names)
    prev_plugins = set(prev.plugin_names)
    curr_plugins = set(curr.plugin_names)

    return {
        "added_tracks": sorted(curr_tracks - prev_tracks),
        "removed_tracks": sorted(prev_tracks - curr_tracks),
        "bpm_changed": (prev.bpm or 0) != (curr.bpm or 0),
        "new_plugins": sorted(curr_plugins - prev_plugins),
        "key_changed": (prev.musical_key or "") != (curr.musical_key or ""),
    }
