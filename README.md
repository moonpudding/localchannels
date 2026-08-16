# LocalChannels

**Your own video library, with channels.** Point LocalChannels at a folder of
videos and it turns them into a browsable, searchable, taggable library with a
built-in player — then shuffles any folder or set of tags into an endless
channel that plays like a TV station.

It's self-hosted and offline by default: no accounts, no cloud, no telemetry.
Your files stay exactly where they are, and nothing leaves the machine unless
you explicitly ask it to (the optional AI summaries and update check are the
only two things that ever talk to the outside world).

<!-- Add a screenshot or two here — the library grid and a channel playing
     make the best first impression. -->

### Why you might want it

- 📁 **It works with your folders as-is.** No importing, no renaming, no
  library format to convert to. Your directory structure *is* the navigation.
- 📺 **Channel mode.** Pick a folder or a few tags and just let it play —
  shuffled without repeats until everything has aired.
- 🏷️ **Tags that live in your filenames.** Read tags out of
  `Title [TAG1 TAG2].mp4`, or write them back into the filenames, so your
  organisation survives even without the database.
- ▶️ **Picks up where you left off**, per video, with watch history and
  favorites.
- 🥽 **Real VR playback**, including immersive WebXR sessions on a headset.
- 🎨 **Nine themes**, dark and light.

---

## Quick start

```bash
git clone <your-repo-url> localchannels
cd localchannels
# point the first volume at your videos
$EDITOR docker-compose.yaml
docker compose up -d --build
```

Open <http://localhost:8000>. Indexing starts automatically in the background —
you can browse while it works.

No Docker? See [Run without Docker](#run-without-docker).

---

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Run with Docker](#run-with-docker)
- [Run without Docker](#run-without-docker)
- [Configuration](#configuration)
- [Themes](#themes)
- [Password protection](#password-protection)
- [Tags and filenames](#tags-and-filenames)
- [How renames and moves are tracked](#how-renames-and-moves-are-tracked)
- [AI video summaries](#ai-video-summaries)
- [Backup and restore](#backup-and-restore)
- [Project metadata and the About page](#project-metadata-and-the-about-page)
- [How things are stored](#how-things-are-stored)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Good to know](#good-to-know)

---

## Features

### 🎬 Browsing and playback

- **File browser** — a collapsible folder tree in the sidebar, with breadcrumbs
  and expand/collapse state remembered between visits
- **Folder view** — a thumbnail grid of everything in the current folder
- **Thumbnail or list view** — a toggle beside the sort controls switches every
  listing between the poster grid and a dense table showing filename, folder,
  file size, file date, date added, resolution, duration and frame rate. The
  choice is remembered per browser, and select mode works in both
- **All Videos** — a flat, paginated view of the whole library (60 per page)
- **Sorting** — order any listing by name, date added, file date, length,
  resolution (pixel count) or file size, ascending or descending
- **Video page** — full metadata table and an inline player with HTTP range
  streaming, so seeking works and the browser only pulls the bytes it needs
- **Resume playback** — remembers where you stopped and offers *Resume* or
  *Play from beginning* when you come back
- **Watched tracking** — a video counts as watched at 90%; watching 5% or less
  doesn't count and leaves no resume point. Cards show a Watched badge, or a
  progress bar when partially watched
- **Watch history** — every play is timestamped, with last-played shown on cards
  and the detail page. The **Watched** page lists the last 100 played
- **New** — the 100 most recently *added* videos, by when they entered the
  library rather than the file's own date
- **Favorites** — heart a video from its page or the hover heart on a card

### 📺 Channel mode

- **Shuffle without repeats** — plays random videos until everything has aired,
  then loops. Start one ad-hoc from any folder or tag
- **Named channels** — build a channel from any combination of folders and tags
  and keep it in the sidebar for later
- **Clean full-screen** — the exit button and overlay fade out with the player
  controls while a channel runs

### 🏷️ Tags and organisation

- **User tags** — add and remove freely, with autocomplete and a one-click
  picker for your 20 most-used tags. The Tags page lists every tag; open one to
  see its videos
- **Tags from filenames** — pull space-separated tags out of a bracketed
  section, e.g. `Channel - Title [TECH OC GUIDE].mp4` → `TECH`, `OC`, `GUIDE`.
  Run it across the library on demand, or auto-apply during scans
- **Write tags into filenames** — the inverse: rename files on disk so each one
  carries its tags. Any existing tag section is replaced rather than stacked,
  untagged videos are left alone, and you always get a preview before anything
  moves
- **Clean titles** — the bracketed tag section is hidden from the displayed
  title, so `Myvideo - VidTitle [TAG1 TAG2].mp4` shows as *Myvideo - VidTitle*.
  Name sorting follows the displayed title too
- **Untagged filter** — a shortcut on the Tags page lists every video with no
  tags, with a live count. Sorting and select mode both work, so it doubles as
  a worklist for tagging up a library
- **Mass tagging and bulk delete** — select multiple videos (or Select all) and
  apply tags to the whole set at once, or delete them from disk

### 🥽 VR and 360°

- **VR playback** — flagged videos play in an interactive WebGL viewer: drag to
  look around, scroll to zoom, with controls for projection (360° / 180° /
  fisheye), stereo layout (mono / side-by-side / top-bottom), recenter and
  fullscreen. Every video page has a VR ⇄ Flat toggle and a *Mark as VR* button
  for content the detector missed; the chosen projection and layout are
  remembered per video
- **WebXR headset playback** — on a WebXR-capable browser (Quest Browser,
  Wolvic, desktop Chrome with a PCVR headset) an **Enter VR** button starts a
  true immersive session: head-tracked, stereoscopic per-eye rendering using the
  video's projection and layout. 360°/180° content plays through a WebXR media
  layer where supported — the compositor samples the video directly, so playback
  is smooth and full-quality — with a WebGL fallback that is always used for
  fisheye. Requires a secure context: HTTPS or localhost
- **In-headset controls** — either controller works: flick the thumbstick
  left/right to skip 10s, hold up/down to zoom, pull the trigger to play/pause,
  press B (or Y) to stop and return to the page. Haptic pulses confirm each
  action
- **VR detection** — recognised from filename tokens (360, 180, VR, SBS, TB,
  MKX200, fisheye, …). The sidebar **VR** section stays hidden until you have
  at least one VR video

### 🤖 AI summaries

- **Any OpenAI-compatible vision model** — vLLM, OpenAI, LM Studio,
  llama.cpp server, and so on. Frames are sampled from the video and sent for a
  summary, which appears on the video page between the player and the metadata
- **Per-video or batch** — analyze one video with its Analyze button, or run the
  whole library or a single folder in the background with live progress. Batches
  skip already-summarized videos unless you ask for re-analysis, and can be
  cancelled mid-run
- Fully optional and off until you configure an endpoint. See
  [AI video summaries](#ai-video-summaries) for setup

### 🗂️ Library management

- **Automatic indexing** — resolution, duration, fps, video/audio codec,
  bitrate, container and file size via `ffprobe`, plus a poster thumbnail via
  `ffmpeg`. Rescans are incremental, so unchanged files are skipped
- **Rename-safe** — renaming or moving a file doesn't create a duplicate entry.
  Its date added, tags, favorite, watch history, resume point and thumbnail all
  follow it. See [how it works](#how-renames-and-moves-are-tracked)
- **Duplicate detection** — find videos that are byte-for-byte identical, share
  an exact file size, or have the same runtime (matched to the millisecond, with
  a selectable tolerance). Remove them from the library or delete the file
  outright; removed files are remembered so a rescan won't re-add them
- **Backup** — one click writes a zip of the whole database to the server and
  downloads a copy through the browser. Your video files are never touched

### 🎨 Appearance and access

- **Nine themes** — Catppuccin (Mocha, Macchiato, Frappé, Latte), Dark, Light,
  Nord, Rosé Pine and Gruvbox. See [Themes](#themes)
- **Configurable site name** in the header and browser tab
- **Optional password protection** — a single shared password, no accounts. See
  [Password protection](#password-protection)

### ⚙️ Settings

Everything above is configured from the cogwheel in the top bar:

| Section | What's there |
|---------|--------------|
| **General** | Theme, autoplay toggle, library backup |
| **Media Management** | Rescan (incremental or full), duplicate detection, and a reset that clears the Watched flag from every video |
| **Tag Management** | Filename tag settings (delimiter, title stripping, auto-tagging), then *Manage Tags* to add, rename and delete tags library-wide |
| **AI Analysis** | Endpoint, model, API key, prompt, frame sampling, and batch runs |
| **About** | Version, licensing, contact details, and a manual update check |

---
## Run with Docker

The image bundles `ffmpeg`, so Docker is the only prerequisite.

**1.** Edit `docker-compose.yaml` and point the first volume at your videos:

```yaml
volumes:
  - /path/to/your/videos:/videos
  - localchannels-data:/data
```

The library is mounted **read/write** because two features change files on
disk: deleting a file from the duplicates page, and writing tags into
filenames. Everything else — indexing, thumbnails, playback — only reads.
Append `:ro` if you'd rather your library stayed untouchable; the app runs fine
either way, and those two features report a read-only error instead.

**2.** Build and start:

```bash
docker compose up -d --build
```

**3.** Open <http://localhost:8000>

The database and thumbnails live in the `localchannels-data` named volume, so
they survive restarts and rebuilds. To keep that data visible on the host
instead, swap the volume line for a bind mount (`- ./data:/data`).

Common commands:

```bash
docker compose logs -f          # watch indexing progress
docker compose restart          # restart
docker compose down             # stop and remove the container
docker compose up -d --build    # rebuild after code changes
```

To rescan the library, use the ↻ button in the UI — no restart needed.

---

## Run without Docker

**Requirements:** Python 3.10+ and `ffmpeg` (which also provides `ffprobe`).

On Arch:

```bash
sudo pacman -S ffmpeg python
```

**Setup:**

```bash
cd localchannels
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Edit `config.json` and set `root_path` to your video folder:

```json
{
  "site_name": "My Media",
  "root_path": "/home/you/Videos",
  "host": "127.0.0.1",
  "port": 8000,
  "default_theme": "mocha"
}
```

**Run:**

```bash
python -m app.main
```

Then open <http://127.0.0.1:8000>. On first launch it starts indexing in the
background. Re-scan any time with the ↻ button — indexing is incremental, so
unchanged files are skipped and thumbnails are cached in `data/thumbnails/`.

For development with live reload:

```bash
uvicorn app.main:app --reload
```

---

## Configuration

Everything can be set in `config.json`, or with environment variables (handy
for Docker, and they win over the file).

| Key | Environment variable | Default | Meaning |
|-----|---------------------|---------|---------|
| `site_name` | `LOCALCHANNELS_SITE_NAME` | `LocalChannels` | Name in the header and browser tab |
| `root_path` | `LOCALCHANNELS_ROOT_PATH` | `./videos` | Absolute path to your video library |
| `data_dir` | `LOCALCHANNELS_DATA_DIR` | `./data` | Where the database and thumbnails are stored |
| `host` | `LOCALCHANNELS_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` to reach it from other devices) |
| `port` | `LOCALCHANNELS_PORT` | `8000` | Port to listen on |
| `default_theme` | `LOCALCHANNELS_DEFAULT_THEME` | `mocha` | Initial theme; each browser remembers its own choice after that |
| `thumbnail_width` | `LOCALCHANNELS_THUMBNAIL_WIDTH` | `480` | Thumbnail width in px (height is automatic) |
| `thumbnail_seek_fraction` | `LOCALCHANNELS_THUMBNAIL_SEEK_FRACTION` | `0.20` | Where in each video the poster frame is grabbed (0.0–1.0) |
| `video_extensions` | `LOCALCHANNELS_VIDEO_EXTENSIONS` | (built-in list) | File types treated as videos. Comma-separated in the env var, e.g. `.mp4,.mkv,.webm` |
| `auth_enabled` | `LOCALCHANNELS_AUTH_ENABLED` | `false` | Turn on shared-password protection |
| `password` | `LOCALCHANNELS_PASSWORD` | (unset) | The password — required for auth to actually activate |

Two more environment variables have no `config.json` equivalent, since they say
where those files live:

| Variable | Default | Meaning |
|----------|---------|---------|
| `LOCALCHANNELS_CONFIG` | `./config.json` | Path to the config file |
| `LOCALCHANNELS_ABOUT` | `./about.json` | Path to the About page metadata |

---

## Themes

Pick a theme in **Settings → General**; your choice is remembered per browser.
Set the starting theme with `default_theme`.

| Key | Theme |
| --- | --- |
| `mocha` | Catppuccin Mocha (default) |
| `macchiato` | Catppuccin Macchiato |
| `frappe` | Catppuccin Frappé |
| `latte` | Catppuccin Latte |
| `dark` | Dark |
| `light` | Light |
| `nord` | Nord |
| `rosepine` | Rosé Pine |
| `gruvbox` | Gruvbox |

---

## Password protection

Set both `LOCALCHANNELS_AUTH_ENABLED=true` and `LOCALCHANNELS_PASSWORD=...` to
require a password. Visitors get a sign-in page; on success a session cookie
(valid ~30 days) is set and a Sign-out button appears in the header. If
`AUTH_ENABLED` is on but no password is set, protection stays off and a warning
is logged.

This is deliberately simple: one shared password, a cookie derived from it, no
user accounts. It's meant to keep casual visitors out on a trusted network, not
as hardened security. Put it behind HTTPS — a reverse proxy with a valid
certificate — if you expose it to the internet.

---

## Tags and filenames

LocalChannels can read tags out of your filenames and write them back, so your
organisation isn't trapped in a database.

**Reading.** A bracketed section at the end of a filename is treated as
space-separated tags: `Channel - Title [TECH OC GUIDE].mp4` yields `TECH`, `OC`
and `GUIDE`. The delimiter is configurable — square brackets, parentheses or
curly braces. Run it across the library from **Settings → Tag Management**, or
turn on auto-tagging to apply it during every scan.

**Displaying.** By default the tag section is hidden from the title shown in
the app, so that file appears as *Channel - Title*. Sorting by name follows the
displayed title too. Files on disk are untouched and the full path is still
shown on the video page. Turn it off with **Hide tags in titles**.

**Writing.** *Write tags into filenames* does the reverse — it renames files on
disk so each carries its current tags. Worth knowing before you run it:

- Any existing tag section is **replaced**, not appended, so running it twice
  changes nothing the second time.
- Videos with **no tags are skipped** entirely. It only ever writes tags out; it
  won't strip a bracket section off a file the app has no tags for.
- Tags containing spaces or characters unsafe in a filename cause that video to
  be **skipped with a reason**, rather than writing something that won't read
  back correctly.
- **Collisions are detected** both against existing files and against other
  videos in the same run.
- You get a **full preview** — every rename, and every skip with its reason —
  and nothing moves until you confirm.
- Renames keep the same library entry, so tags, favorites, watch history and
  resume points all survive.

This needs the library mounted read/write.

---

## How renames and moves are tracked

The database keys videos on their path, but paths change — you rename a file,
reorganise a folder, or run the tag writeback. To keep a video's history
attached to it, the indexer stores a **quick fingerprint** for each file: a
SHA-1 of its size plus its first and last 512 KB.

On each scan, any path that has vanished is matched by fingerprint against any
path that has appeared. A match is treated as a move: the same database row is
kept and only its path changes. That preserves the date added, tags, favorite
flag, watch history, resume point, VR settings, AI summary and cached
thumbnail. Reading a megabyte per file is unnoticeable next to the metadata
probing a scan already does.

The fingerprint is deliberately **not** a full-file hash — hashing whole videos
would make scans unusably slow. It's only ever used to pair a disappeared path
with a new one, never to declare two files duplicates; the duplicates page does
its own full SHA-256 for that.

A copy is not a move: if the original is still in place, the new file is
indexed as a genuinely new video. And if you rename a file *and* change its
contents between scans, the fingerprint changes and it will look new.

---
## AI video summaries

Optional, and off until you configure it. Everything is set at runtime in
**Settings → AI Analysis** and stored in the database, so it survives container
rebuilds. Point the endpoint at any OpenAI-compatible `/v1` base URL.

For a local vLLM instance:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --limit-mm-per-prompt image=16
```

Then in Settings → AI Analysis:

- **Endpoint URL** — `http://your-host:8000/v1`. From Docker, use the host's IP
  or `host.docker.internal`, not `localhost`
- **Model** — `Qwen/Qwen2.5-VL-7B-Instruct`, or whatever name your server expects
- **API key** — leave empty for vLLM unless you started it with `--api-key`; set
  it for OpenAI and other hosted endpoints
- **Max frames** — keep at or below the server's image-per-prompt limit

Use **Test connection** to verify with a cheap text-only request, then analyze a
single video from its page or run a batch over the library or one folder.
Summaries appear on the video page between the player and the metadata table.

Switching providers later is just a change of endpoint, model and key — the
request format is plain OpenAI chat completions with base64 image parts, so
anything speaking that protocol works.

---

## Backup and restore

**Settings → General → Back up now** writes
`data/backups/localchannels-backup.zip` and immediately downloads a timestamped
copy to your browser. The archive contains:

| Entry | What it is |
|-------|------------|
| `library.db` | A consistent snapshot of the database, taken with SQLite's online backup API — safe to run while the app is serving |
| `config.json` | The active config file, if there is one |
| `thumbnails/` | Cached posters, only when **Include thumbnails** is on |
| `manifest.json` | Timestamp, row counts, and what the archive contains |

Only one backup is kept — each run replaces the previous file. It's built to a
temporary file and moved into place atomically, so an interrupted or failed run
leaves the last good backup intact. Under Docker it lands in the `/data`
volume, so it survives rebuilds; copy it off the host if you want it somewhere
safer.

To restore, stop the app and put `library.db` back:

```bash
docker compose stop localchannels      # or Ctrl-C if running directly
unzip -o localchannels-backup.zip library.db -d /path/to/data
rm -f /path/to/data/library.db-wal /path/to/data/library.db-shm
docker compose start localchannels
```

If you didn't include thumbnails, run **Settings → Media Management → Force
full rescan** afterwards to rebuild them.

---

## Project metadata and the About page

**Settings → About** is rendered entirely from `about.json` in the project
root — name, version, tagline, description, license, author, contact details,
links and credits. Nothing there is hardcoded, so licensing and contact info can
be updated without touching code.

Every field is optional; leave one empty and its row is skipped, so a
half-filled file still produces a tidy page.

To edit it without rebuilding the image, mount your own copy over it:

```yaml
volumes:
  - ./about.json:/app/about.json:ro
```

...or point `LOCALCHANNELS_ABOUT` at a path anywhere on disk.

A sensible division of labour:

| What | Where it lives |
|------|----------------|
| License name and link | `about.json` |
| Full license text | the `LICENSE` file — canonical, don't duplicate it |
| Contact, links, credits | `about.json` |
| Released versions, changelogs | GitHub Releases |

### Update checks

**Check for updates** asks the GitHub Releases API for the newest release of
the repo named in `about.json` and compares its tag against `version`. Fill in
`repo.owner` and `repo.name` to enable it; until then the page says there's
nothing to check against.

It only runs when the button is pressed — the app never contacts GitHub on its
own — and the answer is cached for ten minutes so repeated presses don't hit
GitHub's unauthenticated rate limit. Version comparison tolerates a leading `v`
and differing numbers of parts, so `v2.0` is correctly newer than `1.9.9`.

Bump `version` in `about.json` whenever you tag a release so the two match. To
avoid doing that by hand, have the release workflow write it:

```yaml
- name: Stamp version from the tag
  run: |
    python - <<'EOF'
    import json, os, pathlib
    tag = os.environ["GITHUB_REF_NAME"].lstrip("v")
    path = pathlib.Path("about.json")
    data = json.loads(path.read_text())
    data["version"] = tag
    path.write_text(json.dumps(data, indent=2) + "\n")
    EOF
```

---

## How things are stored

Everything the app generates lives in `data/`:

| Path | What it is |
|------|------------|
| `data/library.db` | SQLite database — videos, watch history, tags, channels, settings |
| `data/thumbnails/` | Cached JPEG posters |
| `data/backups/localchannels-backup.zip` | The most recent backup, if you've made one |

`config.json` and `about.json` sit in the project root and are part of the
repo, not of your data.

Video paths are stored **relative to `root_path`**, so you can move the whole
library to another machine and keep your history and tags, as long as the
videos keep the same relative layout.

---

## Keyboard shortcuts

In channel mode:

| Key | Action |
|-----|--------|
| `N` or `→` | Next video |
| `Esc` | Exit the channel |

---

## Good to know

- **Browser Back and Forward work.** Every page is a hash route, so Back returns
  to the previous page instead of leaving the site, and links like `#/video/42`
  are shareable.
- **Autoplay is off by default.** Toggle it in Settings → General (remembered
  per browser). When off, opening a video shows its poster and waits for you to
  press play, and it only counts as watched once playback actually starts.
  Channel mode always plays continuously — that's the point of a channel.
- **Seeking is efficient.** Streaming supports HTTP range requests, so the
  browser only pulls the bytes it needs.
- **Path traversal is blocked.** The server only serves files that resolve
  inside `root_path`.
- **Assets are cache-busted per build**, so a rebuild always loads the new UI —
  no hard refresh needed.
- **To reach it from other devices**, set `host` to `0.0.0.0` and browse to your
  machine's IP. Turn on [password protection](#password-protection) if the
  network isn't fully trusted.
