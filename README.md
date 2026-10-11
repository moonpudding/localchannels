# LocalChannels

**Your own video library, with channels.** Point LocalChannels at a folder of
videos and it turns them into a browsable, searchable, taggable library with a
built-in player — then shuffles any folder or set of tags into an endless
channel that plays like a TV station.

It's self-hosted and offline by default: no accounts, no cloud, no telemetry.
Your files stay exactly where they are, and nothing leaves the machine unless
you ask it to. The only two things that ever make a network request are the
optional AI summaries (to the endpoint you configure, which can be a model on
your own network) and the update check, which runs only when you press it.

<!-- Add a screenshot or two here — the library grid and a channel playing
     make the best first impression. -->

### Why you might want it

- 📁 **It works with your folders as-is.** No importing, no renaming, no
  library format to convert to. Your directory structure *is* the navigation.
- 📺 **Channel mode.** Pick a folder or a few tags and just let it play —
  shuffled (or in filename order) without repeats until everything has aired.
- 🏷️ **Tags that live in your filenames.** Read tags out of
  `Title [TAG1 TAG2].mp4`, or write them back into the filenames, so your
  organisation survives even without the database. Drill down by combining
  tags to include and exclude.
- 🤖 **AI video summaries.** Point it at any OpenAI-compatible vision model —
  a local vLLM, LM Studio or llama.cpp server, or a hosted API — and it
  describes each video from sampled frames, one at a time or the whole library
  in the background.
- ▶️ **Picks up where you left off**, per video, with watch history and
  favorites.
- 🧰 **Library housekeeping.** Find duplicates, export CSV media reports, and
  list the files browsers can't play, with a ready-made ffmpeg fix for each.
- 🥽 **Real VR playback**, including immersive WebXR sessions on a headset.
- 🎨 **Nine themes**, dark and light.

---

## Quick start

Create a `docker-compose.yaml` (or modify the one in the repo):

```yaml
services:
  localchannels:
    image: ghcr.io/moonpudding/localchannels:latest
    container_name: localchannels
    ports:
      - "8000:8000"
    volumes:
      - /path/to/your/videos:/videos     # ← your library
      - localchannels-data:/data         # database + thumbnails
    restart: unless-stopped

volumes:
  localchannels-data:
```

```bash
docker compose up -d
```

Open <http://localhost:8000>. Indexing starts automatically in the background —
you can browse while it works.

More options in [Run with Docker](#run-with-docker). No Docker? See
[Run without Docker](#run-without-docker).

---

## Contents

- [Features](#features)
- [Run with Docker](#run-with-docker)
- [Run without Docker](#run-without-docker)
- [Configuration](#configuration)
- [Themes](#themes)
- [Password protection](#password-protection)
- [Filtering by tags](#filtering-by-tags)
- [Tags and filenames](#tags-and-filenames)
- [Browser playback and file formats](#browser-playback-and-file-formats)
- [Media reports](#media-reports)
- [Ignoring duplicate groups](#ignoring-duplicate-groups)
- [How renames and moves are tracked](#how-renames-and-moves-are-tracked)
- [AI video summaries](#ai-video-summaries)
- [Backup and restore](#backup-and-restore)
- [Project metadata and the About page](#project-metadata-and-the-about-page)
- [How things are stored](#how-things-are-stored)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Good to know](#good-to-know)
- [Releases](#releases)

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
- **Video page** — full metadata table (including file date and date added) and
  an inline player with HTTP range streaming, so seeking works and the browser
  only pulls the bytes it needs
- **Clear playback errors** — when the browser can't play a file, the player
  says why (unsupported container, codec it can't decode, decoder failure, or a
  missing file) and suggests the ffmpeg command that fixes it, instead of
  sitting on a blank screen. See
  [Browser playback and file formats](#browser-playback-and-file-formats)
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

- **No repeats** — plays every matching video once, then loops. Order is
  random by default, or filename order with *Settings → General → Channel
  order*. Start one ad-hoc from any folder, tag or tag filter
- **Named channels** — build a channel from any combination of folders and tags
  and keep it in the sidebar. Edit the rule later from the pencil on the
  sidebar entry
- **Include and exclude tags** — a channel can require tags *and* bar them:
  "anything tagged tag1, tag2 or tag3, but nothing tagged tag6 or tag7". The
  builder shows a live count of what the rule matches before you save it
- **Skips what won't play** — a file the browser can't play is skipped with a
  short notice rather than stalling the channel; after five failures in a row
  the channel stops and says so
- **Clean full-screen** — the exit button and overlay fade out with the player
  controls while a channel runs

### 🏷️ Tags and organisation

- **User tags** — add and remove freely, with autocomplete and a one-click
  picker for your 20 most-used tags. The Tags page lists every tag; open one to
  see its videos
- **Tag filter with drill-down** — *Tags → Filter by tags* combines tags into
  one query: click a tag once to require it, again to exclude it. As you pick
  tags, the list narrows to tags that still appear in the results, with counts
  of how many results carry each. Results can be played or saved as a channel
  in a click. See [Filtering by tags](#filtering-by-tags)
- **Tags from filenames** — pull space-separated tags out of a bracketed
  section, e.g. `Channel - Title [TECH OC GUIDE].mp4` → `TECH`, `OC`, `GUIDE`.
  Run it across the library on demand, or auto-apply during scans
- **Write tags into filenames** — the inverse: rename files on disk so each one
  carries its tags. Any existing tag section is replaced rather than stacked,
  untagged videos are left alone, and you always get a preview before anything
  moves. A toolbar warning (optional) flags tags not yet written out
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
- **VR detection** — recognised from filename tokens (VR, 180, 360, SBS, TB,
  3DH, MKX200, fisheye, …). The sidebar **VR** section stays hidden until you
  have at least one VR video
- **Playback report** — an optional diagnostic (*Settings → General →
  Debugging*) that shows refresh rate, frame cadence and buffer depth after
  each immersive session, for tracking down stutter

### 🤖 AI summaries

- **Any OpenAI-compatible vision model** — vLLM, OpenAI, LM Studio,
  llama.cpp server, and so on. Frames are sampled from the video and sent for a
  summary, which appears on the video page between the player and the metadata
- **Frame sampling** — a fixed number of frames spread across the video, or
  one frame every N seconds, with a hard cap and a configurable frame width
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
- **Ignore a duplicate group** — some matches are meant to be there: two cuts of
  the same film with different soundtracks share a runtime on purpose. Dismiss
  the group and it stops appearing — until another copy turns up, at which point
  it comes back for review. See [Ignoring duplicate
  groups](#ignoring-duplicate-groups)
- **Media reports** — export a CSV for the whole library or one folder (with or
  without subfolders), straight from the index or with every file re-probed for
  tracks, languages, embedded tags and chapters. See [Media reports](#media-reports)
- **Browser-incompatible files** — lists files browsers can't play, with a fix
  command for each, a downloadable fix script, and a checklist of originals
  that are safe to delete once converted. See
  [Browser playback and file formats](#browser-playback-and-file-formats)
- **Thumbnail cleanup** — posters are deleted along with their videos, and every
  scan sweeps the cache for anything left behind. *Settings → Media Management*
  shows how many stale thumbnails there are and can clean them up on demand
- **Watch data resets** — clear the Watched flag from every video, or clear the
  whole play history (resume points are kept either way)
- **Backup** — one click writes a zip of the whole database to the server and
  downloads a copy through the browser. Your video files are never touched

### 🎨 Appearance and access

- **Nine themes** — Catppuccin (Mocha, Macchiato, Frappé, Latte), Plain Dark,
  Plain Light, Nord, Rosé Pine and Gruvbox. See [Themes](#themes)
- **Configurable site name** in the header and browser tab
- **Optional password protection** — a single shared password, no accounts. See
  [Password protection](#password-protection)

### ⚙️ Settings

Everything above is configured from the cogwheel in the top bar:

| Section | What's there |
|---------|--------------|
| **General** | Theme, autoplay, channel order, library backup, and a Debugging group (VR playback report) |
| **Media Management** | Rescan (incremental or full), duplicate detection, media report (CSV), browser-incompatible files, thumbnail cache cleanup, reset watched status, clear play history |
| **Tag Management** | Filename tag settings (delimiter, title stripping, auto-tagging, unwritten-tag warning), writing tags into filenames, then *Manage Tags* to add, rename and delete tags library-wide |
| **AI Analysis** | Endpoint, model, API key, reasoning toggle, frame sampling, system prompt, and batch runs |
| **Statistics** | Library totals, watch progress, quality breakdown, most played, viewing habits, records and more |
| **About** | Version, licensing, contact details, and a manual update check |

---

## Run with Docker

Images are published to the GitHub Container Registry for `linux/amd64` and
`linux/arm64` (Raspberry Pi and ARM NAS boxes), with `ffmpeg` bundled, so Docker
is the only prerequisite.

### docker compose

A fuller `docker-compose.yaml` than the one in the quick start:

```yaml
services:
  localchannels:
    image: ghcr.io/moonpudding/localchannels:latest
    container_name: localchannels
    ports:
      - "8000:8000"
    volumes:
      # Your video library. Read/write — see the note below.
      - /path/to/your/videos:/videos
      # Database + thumbnails. Swap for `- ./data:/data` to keep them on the host.
      - localchannels-data:/data
    environment:
      LOCALCHANNELS_SITE_NAME: "LocalChannels"
      LOCALCHANNELS_DEFAULT_THEME: "mocha"
      # Password protection (off by default). Set BOTH to enable:
      # LOCALCHANNELS_AUTH_ENABLED: "true"
      # LOCALCHANNELS_PASSWORD: "change-me"
      # Optional tuning:
      # LOCALCHANNELS_THUMBNAIL_WIDTH: "480"
      # LOCALCHANNELS_THUMBNAIL_SEEK_FRACTION: "0.20"
      # LOCALCHANNELS_VIDEO_EXTENSIONS: ".mp4,.mkv,.webm,.avi,.mov,.m4v,.ts"
    restart: unless-stopped

volumes:
  localchannels-data:
```

```bash
docker compose up -d
```

The library is mounted **read/write** because a few features change files on
disk: deleting files (from the duplicates page, bulk delete, or the
incompatible-files list) and writing tags into filenames. Everything else —
indexing, thumbnails, playback, reports — only reads. Append `:ro` to the
library volume if you'd rather it stayed untouchable; the app runs fine either
way, and those features report a read-only error instead.

The database and thumbnails live in the `localchannels-data` named volume, so
they survive restarts, updates and rebuilds.

### Image tags

| Tag | What you get |
|-----|--------------|
| `latest` | The newest release. A good default |
| `1` | The newest 1.x release — new features and fixes, nothing breaking |
| `1.1` | The newest 1.1.x release — fixes only |
| `1.1.0` | Exactly that release, forever |
| `edge` | The newest commit on `main`, released or not. May be rough |

### Updating

```bash
docker compose pull
docker compose up -d
```

Your library data is in the volume, so nothing is lost. **Settings → About →
Check for updates** tells you when a new release is out.

### Plain docker

```bash
docker run -d --name localchannels \
  -p 8000:8000 \
  -v /path/to/your/videos:/videos \
  -v localchannels-data:/data \
  --restart unless-stopped \
  ghcr.io/moonpudding/localchannels:latest
```

### Building the image yourself

To run local changes, replace the `image:` line in `docker-compose.yaml` with
`build: .` (in a clone of this repo) and start it with:

```bash
docker compose up -d --build
```

### Common commands

```bash
docker compose logs -f          # watch indexing progress
docker compose restart          # restart
docker compose down             # stop and remove the container (data is kept)
```

To rescan the library, use the ↻ button in the UI — no restart needed. The
container has a health check on `/api/health`, so `docker ps` shows whether
it's up.

---

## Run without Docker

**Requirements:** Python 3.10+ and `ffmpeg` (which also provides `ffprobe`).

On Arch:

```bash
sudo pacman -S ffmpeg python
```

**Setup:**

```bash
git clone https://github.com/moonpudding/localchannels.git
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
| `root_path` | `LOCALCHANNELS_ROOT_PATH` | `./videos` (`/videos` in Docker) | Absolute path to your video library |
| `data_dir` | `LOCALCHANNELS_DATA_DIR` | `./data` (`/data` in Docker) | Where the database and thumbnails are stored |
| `host` | `LOCALCHANNELS_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Bind address (`0.0.0.0` to reach it from other devices) |
| `port` | `LOCALCHANNELS_PORT` | `8000` | Port to listen on |
| `default_theme` | `LOCALCHANNELS_DEFAULT_THEME` | `mocha` | Initial theme; each browser remembers its own choice after that |
| `thumbnail_width` | `LOCALCHANNELS_THUMBNAIL_WIDTH` | `480` | Thumbnail width in px (height is automatic) |
| `thumbnail_seek_fraction` | `LOCALCHANNELS_THUMBNAIL_SEEK_FRACTION` | `0.20` | Where in each video the poster frame is grabbed (0.0–1.0) |
| `video_extensions` | `LOCALCHANNELS_VIDEO_EXTENSIONS` | `.mp4 .mkv .webm .avi .mov .m4v .flv .wmv .mpg .mpeg .ts` | File types treated as videos. Comma-separated in the env var, e.g. `.mp4,.mkv,.webm` |
| `auth_enabled` | `LOCALCHANNELS_AUTH_ENABLED` | `false` | Turn on shared-password protection |
| `password` | `LOCALCHANNELS_PASSWORD` | (unset) | The password — required for auth to actually activate |

Two more environment variables have no `config.json` equivalent, since they say
where those files live:

| Variable | Default | Meaning |
|----------|---------|---------|
| `LOCALCHANNELS_CONFIG` | `./config.json` | Path to the config file |
| `LOCALCHANNELS_ABOUT` | `./about.json` | Path to the About page metadata |

Everything else — tag settings, AI settings, channel order, autoplay — is set
in the Settings page and stored in the database, so it survives container
updates.

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
| `dark` | Plain Dark |
| `light` | Plain Light |
| `nord` | Nord |
| `rosepine` | Rosé Pine |
| `gruvbox` | Gruvbox |

---

## Password protection

Set both `LOCALCHANNELS_AUTH_ENABLED=true` and `LOCALCHANNELS_PASSWORD=...` to
require a password. Visitors get a sign-in page; on success a session cookie
(valid 30 days) is set and a Sign-out button appears in the header. If
`AUTH_ENABLED` is on but no password is set, protection stays off and a warning
is logged.

This is deliberately simple: one shared password, a cookie derived from it, no
user accounts. It's meant to keep casual visitors out on a trusted network, not
as hardened security. Put it behind HTTPS — a reverse proxy with a valid
certificate — if you expose it to the internet.

---

## Filtering by tags

**Tags → Filter by tags** turns the tag list into a query. Each tag is a chip
with three states, and clicking cycles through them:

| State | Sign | Meaning |
|-------|------|---------|
| neutral | | the tag is not part of the filter |
| included | `+` | the video must carry it |
| excluded | `−` | the video must not carry it |

### Drilling down

The filter starts in **Match all**: every tag you include narrows the results.
After each click, the tag list updates to the tags that still appear in the
current results, and each tag's number says how many of those results carry
it — so clicking a tag showing `12` leaves you with exactly 12 videos. Tags
that no remaining video carries are hidden (a note says how many), and the
counts cover every result, not just the current page.

Excluded tags take their videos out of the counts too. Including tag2 and
excluding tag3 shows the videos tagged tag2 that aren't tagged tag3, and the
other tags' counts describe just those videos.

### Match any

The **Match all / Match any** button appears once you've included a tag.
**Match any** widens instead: a video needs only one of the included tags, so
"everything tagged tag1, tag3 or tag4" is three includes on Match any. Since
each extra tag adds videos rather than removing them, Match any shows the full
tag list with library-wide counts.

Exclusion is never a vote in either mode — one excluded tag drops a video
regardless of how many included ones it has.

### Using the results

The filter lives in the URL, so a result set can be bookmarked, shared, and
reached again with the browser's Back button. Sorting, the thumbnail/list
toggle, pagination and select mode all work on the results, which makes the
page a reasonable worklist for retagging as well as for browsing.

Two buttons sit with the filter itself:

- **Play as channel** starts an unsaved channel from exactly the videos on
  screen — nothing is stored, and the rotation is keyed to the rule, so
  different filters never share an "already played" list
- **Save as channel…** opens the channel builder with the filter already loaded

The topbar search box is a separate filter with the same chips and the same
drill-down; the two are kept apart deliberately, so typing in the search box
doesn't wipe out a filter you were building on the Tags page.

### Channels from a tag rule

The channel builder takes the same include/exclude chips, plus folders. A video
is in the channel when it is in one of the chosen folders **or** matches the
included tags, and then only if it carries none of the excluded ones. A live
count under the chips shows how many videos the rule currently matches, so an
empty channel is obvious before you save it rather than after you press play.

Including and excluding the same tag is rejected rather than saved, since the
result could only ever be empty.

Saved channels can be edited from the pencil on their sidebar entry. Changing a
channel's rule clears its rotation, because the old "already played" list
describes a channel that no longer exists.

---

## Tags and filenames

LocalChannels can read tags out of your filenames and write them back, so your
organisation isn't trapped in a database.

**Reading.** A bracketed section at the end of a filename is treated as
space-separated tags: `Channel - Title [TECH OC GUIDE].mp4` yields `TECH`, `OC`
and `GUIDE`. The delimiter is configurable — square brackets, parentheses or
curly braces. Run it across the library from **Settings → Tag Management**, or
turn on **Auto-tag on scan** to apply it during every scan.

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

With **Warn about unwritten tags** on (the default), a warning icon in the
toolbar shows when tags have been assigned in the app but not yet written into
filenames.

This needs the library mounted read/write.

---

## Browser playback and file formats

LocalChannels streams your files to the browser exactly as they are — it never
transcodes. Whether a file plays is therefore up to the browser, and most
problems come down to the container or codec.

| | Plays everywhere | Depends on the browser | Doesn't play in browsers |
|---|---|---|---|
| **Container** | MP4, MOV, WebM, MKV | | AVI, MPEG-TS (`.ts`, `.m2ts`), FLV, WMV/ASF, MPEG-PS (`.mpg`) |
| **Video** | H.264, AV1, VP9, VP8 | HEVC/H.265 (needs hardware decoding) | MPEG-4 Part 2 (Xvid/DivX), MPEG-2, WMV/VC-1 |
| **Audio** | AAC, MP3, Opus, Vorbis, FLAC | PCM | AC-3, E-AC-3, DTS, MP2, WMA |

When a file won't play, the player explains which of these it hit and shows the
ffmpeg command that fixes it. Two cases are worth knowing:

- **No picture, but sound plays** — the browser can read the file but not the
  video codec. HEVC is the usual cause: Chrome needs a hardware HEVC decoder on
  Linux and Windows, and Firefox on Linux decodes it through VA-API only.
- **Plays in one browser but not another** — usually hardware decoding. On
  Linux, Chromium with GPU video decoding enabled can fail on codecs it would
  otherwise decode in software (AV1 on some NVIDIA setups, for instance). Try
  another browser, or turn hardware video decoding off, before converting
  anything.

### Finding and fixing incompatible files

**Settings → Media Management → Browser-incompatible files** lists every file
with a problem, for the whole library or one folder, grouped by issue:
unsupported container, unsupported video or audio codec, HEVC, and extensions
that don't match the real container (an `.mp4` that is really Matroska, say).

Each file comes with the cheapest fix:

- **Wrong extension only** — a rename (`mv -n`) to the extension that matches
  the container.
- **Everything else** — a conversion to MP4 next to the original. Streams
  browsers already play are copied as-is (lossless, takes seconds); the rest are
  re-encoded to H.264/AAC. Every audio track is kept; subtitles and data
  streams (teletext, DVB subtitles, EPG in TV recordings) are dropped, because
  MP4 can't carry most of them and they would abort the conversion.

Copy a single command, or download the whole list (filtered by issue if you
like) as a **fix script**:

```bash
bash localchannels-fix-library.sh /path/to/your/videos
```

Run it on the machine that holds the files — under Docker, that's the host
folder you mounted at `/videos`. It never overwrites anything (a file whose
output already exists is skipped and reported), and it keeps the originals.

After a rescan, each original whose converted copy is in the library is marked
**Converted copy in the library**, with a **Delete original** button — so the
same page doubles as a checklist of what's safe to remove.

---

## Media reports

**Settings → Media Management → Media report** downloads a CSV for the whole
library or one folder, with or without its subfolders.

**Quick** reads from the index, so it's instant even for a big library. Columns
include path, folder, extension, container, extension/container mismatch,
video and audio codec, resolution, fps, duration, bitrate, size, browser
playability, the issues found and the fix command, your tags, favorite and
watched flags, play count, last played, date added and file date.

**Detailed** adds what the index doesn't keep, by re-running `ffprobe` on every
file: format name, video/audio/subtitle track counts, audio and subtitle
languages, embedded title, artist, album, comment, copyright, encoder and
creation time (for MPEG-TS recordings, the service name), chapters, and all
container tags as JSON. Files are probed four at a time and rows stream out as
they finish, so the download fills in gradually on a large folder instead of
timing out. A file that can't be probed gets a `probe_error` instead of
stopping the report.

The file is UTF-8 with a byte-order mark, so Excel and LibreOffice both read
non-ASCII filenames correctly.

---

## Ignoring duplicate groups

Not every duplicate is a mistake. Two rips of the same concert with different
audio tracks will match on runtime forever, and both are worth keeping.
**Ignore group** on any group in *Settings → Media Management → Find duplicates*
dismisses it, and a **Show ignored (N)** toggle in the toolbar brings the
dismissed ones back into view (dimmed, with a **Stop ignoring** button)
whenever you want to revisit a decision.

What makes this more than a mute button is what gets stored. An ignore records
the exact set of videos that were in the group at the time, not a fingerprint of
the match. A freshly-found group stays hidden while its members are a *subset*
of a set you already dismissed, which gives two behaviours from one rule:

- **Delete one of three ignored copies** and the remaining two are still covered
  by the record, so you aren't asked again about a decision you already made
- **A fourth copy appears** and the group is no longer a subset of anything, so
  it comes back for review. Dismiss it again and the new, larger record replaces
  the old one rather than stacking on top of it

Because the ignore is a set of videos rather than a property of the match, it
carries across detection methods: a group dismissed under *Same length* is also
hidden under *Identical files*, as long as the members are the same files. A
wider match that pulls in extra videos is a different set, so it is shown.

Ignores are stored in the database and travel with your backups. Deleting a
video removes it from every ignore it was part of, and an ignore that drops
below two members is discarded, so a record can never point at files that have
left the library.

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
updates. Point the endpoint at any OpenAI-compatible `/v1` base URL.

For a local vLLM instance:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --limit-mm-per-prompt image=16
```

Then in Settings → AI Analysis:

- **Endpoint URL** — `http://your-host:8000/v1`. From Docker, use the host's IP
  or `host.docker.internal`, not `localhost` (which is the container itself)
- **Model** — `Qwen/Qwen2.5-VL-7B-Instruct`, or whatever name your server expects
- **API key** — leave empty for vLLM unless you started it with `--api-key`; set
  it for OpenAI and other hosted endpoints
- **Sampling mode** — *fixed count* spreads a set number of frames (default 8)
  evenly across the video; *time-based* takes one frame every N seconds
  (default 60), so longer videos get more frames
- **Max frames** — a hard cap either way (default 16). Keep it at or below the
  server's image-per-prompt limit
- **Frame width** — frames are downscaled to this width before upload (default
  512 px). Smaller is faster and cheaper; larger shows more detail
- **System prompt** — what the model is asked to do with the frames

Use **Test connection** to verify with a cheap text-only request, then analyze a
single video from its page or run a batch over the library or one folder.
Summaries appear on the video page between the player and the metadata table.

Switching providers later is just a change of endpoint, model and key — the
request format is plain OpenAI chat completions with base64 image parts, so
anything speaking that protocol works.

### When the connection test fails

**Test connection** reports what actually went wrong, in the settings page
itself. The three answers worth knowing:

| Message | What it means |
|---------|---------------|
| `… returned HTTP 502/503/504` | A proxy in front of the model server answered, not the model. The server is down, restarting, or the proxy's read timeout is shorter than the model takes to reply |
| `No response from … within 45s` | The endpoint accepted the connection and then went quiet — usually a model still loading, or one swapped to CPU |
| `The model used its whole token budget on reasoning…` | A reasoning model (Qwen3 and similar) thought until it ran out of tokens and never answered |

For the last one, turn on **Skip reasoning tokens**. It sends
`chat_template_kwargs: {"enable_thinking": false}`, which vLLM passes to the
chat template. It is off by default because a server that doesn't recognise the
field rejects the whole request — the OpenAI API among them. Any `<think>`
block that does come back is stripped from the stored summary either way.

Reasoning also explains slowness rather than failure: every summary pays for
hundreds of thinking tokens before the first useful word, which is why a smaller
non-reasoning model can be dramatically faster on the same hardware.

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

The database holds everything you've done in the app: tags, favorites, watch
history, resume points, channels, duplicate ignores, AI summaries and settings.

Only one backup is kept — each run replaces the previous file. It's built to a
temporary file and moved into place atomically, so an interrupted or failed run
leaves the last good backup intact. Under Docker it lands in the `/data`
volume, so it survives updates; copy it off the host if you want it somewhere
safer.

To restore, stop the app and put `library.db` back. With the default named
volume, copy it in through the container:

```bash
unzip -o localchannels-backup.zip library.db -d /tmp/lc-restore
docker compose stop localchannels
docker cp /tmp/lc-restore/library.db localchannels:/data/library.db
docker compose run --rm --entrypoint sh localchannels \
  -c 'rm -f /data/library.db-wal /data/library.db-shm'
docker compose start localchannels
```

With a bind mount (`./data:/data`) or without Docker, it's just files:

```bash
docker compose stop localchannels      # or Ctrl-C if running directly
unzip -o localchannels-backup.zip library.db -d ./data
rm -f ./data/library.db-wal ./data/library.db-shm
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
the repo named in `about.json` (`repo.owner` / `repo.name`) and compares its
tag against `version`. Until those are filled in, the page says there's nothing
to check against.

It only sees **published releases**: a bare git tag, a draft or a pre-release
doesn't count. It only runs when the button is pressed — the app never contacts
GitHub on its own — and the answer is cached for ten minutes so repeated
presses don't hit GitHub's unauthenticated rate limit. Version comparison
tolerates a leading `v` and differing numbers of parts, so `v2.0` is correctly
newer than `1.9.9`.

---

## How things are stored

Everything the app generates lives in the data directory (`data/`, or `/data`
in Docker):

| Path | What it is |
|------|------------|
| `library.db` | SQLite database — videos, watch history, tags, channels, AI summaries, settings |
| `thumbnails/` | Cached JPEG posters |
| `backups/localchannels-backup.zip` | The most recent backup, if you've made one |

`config.json` and `about.json` sit in the project root and are part of the
repo (and the image), not of your data.

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
- **Assets are cache-busted per build**, so an update always loads the new UI —
  no hard refresh needed.
- **To reach it from other devices**, set `host` to `0.0.0.0` (the Docker image
  already does) and browse to your machine's IP. Turn on
  [password protection](#password-protection) if the network isn't fully
  trusted.
- **WebXR and copying commands need a secure context** on other devices.
  Browsers only allow headset sessions over HTTPS or on localhost; on plain
  HTTP the *Copy command* buttons fall back to an older copy method that works
  in most browsers.

---

## Releases

For maintainers. Images are built by `.github/workflows/docker-publish.yaml`:

- **Every push to `main`** publishes `:edge`.
- **Every version tag** (`v1.2.3`) publishes `:1.2.3`, `:1.2`, `:1` and
  `:latest`, after checking that `about.json` carries the same version.
  Pre-release tags (`v1.2.3-rc1`) publish only their own tag.
- **Pull requests** build both architectures but push nothing.
- After pushing, the workflow pulls the published image, starts it and checks
  `/api/health` and the reported version.

To cut a release:

```bash
# 1. set "version": "1.2.0" in about.json, commit, push
git commit -am "Release 1.2.0"
git push origin main

# 2. tag that commit and push the tag
git tag v1.2.0
git push origin v1.2.0

# 3. once the workflow is green, publish a GitHub Release
gh release create v1.2.0 --generate-notes
```

Step 3 is what the in-app update check sees. Use patch versions (`1.2.1`) for
fixes, minor (`1.3.0`) for new features and major (`2.0.0`) for changes that
break existing setups.

If the version check fails because `about.json` wasn't bumped, nothing was
published: delete the tag (`git tag -d v1.2.0 && git push origin
:refs/tags/v1.2.0`), fix, and tag again. Once a version's images are out, don't
reuse its number — release the fix as the next patch.
