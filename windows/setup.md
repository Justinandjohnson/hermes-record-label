# AI Record Label — Windows Setup Guide

This guide walks you through getting the file watcher running on your Windows PC so that whenever you export a track from Ableton Live, the label is automatically notified.

---

## What you need

- **Python 3.11 or newer** — the watcher is a Python script.
  Download it at https://www.python.org/downloads/ — choose the latest Windows installer.
  **During install, check the box that says "Add Python to PATH".** This is easy to miss and required.

- **This folder** (`windows/`) — the three files inside it:
  - `watcher.py` — the watcher script
  - `config.example.json` — the config template
  - `launch.bat` — the double-click launcher

---

## Step 1 — Get your tunnel URL

The Mac runs a Cloudflare tunnel so your Windows PC can reach it over the internet.

- **Option A:** Look at the Mac terminal where the label is running. You'll see a line like:
  ```
  Tunnel URL: https://some-words-here.trycloudflare.com
  ```
- **Option B:** The label may have texted or emailed you the URL.

Copy the full URL including `https://`. You'll paste it into the config in Step 3.

---

## Step 2 — Get your API token

On the Mac, open Terminal and run:

```
cat ~/Library/Application\ Support/ai-record-label/api_token.txt
```

It will print a long string of random characters — that is your token. Copy it.

---

## Step 3 — Create your config.json

1. Open the `windows` folder in File Explorer.
2. Right-click `config.example.json` → **Copy**.
3. Right-click an empty area → **Paste**. A new file appears (`config.example - Copy.json` or similar).
4. Rename it to **`config.json`** (remove the old name entirely).
5. Right-click `config.json` → **Open with** → **Notepad** (or any text editor).
6. Fill in the four values:

```json
{
  "remote_url": "https://the-url-from-step-1.trycloudflare.com",
  "api_token": "the-token-from-step-2",
  "watch_folder": "C:\\Users\\YourName\\Music\\Ableton Live Projects",
  "project_folder": "C:\\Users\\YourName\\Music\\Ableton Live Projects"
}
```

> **Important:** Use double backslashes `\\` in Windows paths, not single `\`.

Save and close Notepad.

---

## Step 4 — Find your Ableton projects folder

Ableton Live typically saves projects to one of these locations:

| Ableton version | Default location |
|---|---|
| Live 11 / 12 | `C:\Users\YourName\Music\Ableton\Projects` |
| Older versions | `C:\Users\YourName\Documents\Ableton` |

To find out for sure:
1. Open Ableton Live.
2. Go to **Options** → **Preferences** → **Library**.
3. Look at the **User Library** path — your projects are usually in the same area.

Use that path for both `watch_folder` and `project_folder` in your config.

---

## Step 5 — Run the watcher

Double-click **`launch.bat`**.

A black console window opens. The launcher will:
1. Confirm Python is installed.
2. Confirm your `config.json` exists.
3. Install `watchdog` automatically if needed (first run only).
4. Start the watcher.

**The window staying open means the watcher is running.** You can minimise it; it will keep working.

---

## Step 6 — Verify it's working

When the watcher starts successfully, you should see output like this:

```
============================================================
  AI Record Label — File Watcher
============================================================
  Watching : C:\Users\YourName\Music\Ableton Live Projects
  API URL  : https://your-tunnel.trycloudflare.com
  Token    : abc12345************************
  Started  : 2026-05-16 14:30:00
============================================================
  Press Ctrl+C to stop.

2026-05-16 14:30:01  INFO      Observer started — watching for audio files …
```

To test it:
- Export or bounce a track from Ableton to your watched folder.
- Within a few seconds you should see a line like:
  ```
  2026-05-16 14:31:05  INFO      New audio file detected: C:\...\my_track.wav
  2026-05-16 14:31:05  INFO      Sending event (attempt 1/3): my_track → https://...
  2026-05-16 14:31:06  INFO      Event accepted  [HTTP 200]  title='my_track'
  ```

If you see `HTTP 200`, the label received the track. You're done.

---

## Step 7 — Stopping the watcher

Press **Ctrl+C** in the console window, or simply close the window.

---

## Troubleshooting

### "Python was not found"

Python is not installed or was installed without the "Add to PATH" option.

- Download Python from https://www.python.org/downloads/
- Run the installer and **check "Add Python to PATH"** at the bottom of the first screen.
- Restart any open console windows after installing.

---

### "config.json not found"

You haven't created `config.json` yet. Follow Step 3 above.

---

### "watch_folder does not exist or is not a directory"

The path you put in `watch_folder` doesn't exist on this PC.

- Check the path in Ableton → Preferences → Library.
- Make sure you used double backslashes: `C:\\Users\\YourName\\...`

---

### "HTTP error 401" or "HTTP error 403"

Your `api_token` is wrong.

- Re-run the command on the Mac to get the correct token:
  ```
  cat ~/Library/Application\ Support/ai-record-label/api_token.txt
  ```
- Paste it into `config.json` and try again.

---

### "Connection error" or "Request timed out"

The tunnel URL may have changed. Cloudflare tunnels get a new URL each time the Mac restarts the label.

- Ask the Mac operator for the current tunnel URL.
- Update `remote_url` in `config.json` and restart the watcher.

---

### Nothing happens when I export from Ableton

- Make sure Ableton is exporting to the folder listed in `watch_folder`.
- The watcher only detects `.wav`, `.aif`, `.aiff`, `.flac`, and `.mp3` files.
- Check that the console window is still open and shows no errors.
