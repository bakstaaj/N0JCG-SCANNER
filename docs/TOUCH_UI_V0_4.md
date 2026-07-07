# V0.4A Touch UI

V0.4A replaces the desktop-oriented dashboard with a touch-first layout aimed at an 800x480 Raspberry Pi 7 inch display.

## Main screen

The dashboard is intentionally simple:

- Start Scanner + Audio
- Stop
- Browser audio player
- Active talkgroup details
- Key runtime statistics

The Start button is the primary user gesture. It starts the scanner and attaches the browser audio stream from `http://<page-host>:8072/audio.wav`.

## Hamburger menu

The menu separates less-common tasks from the main screen:

- Dashboard
- Radio Setup Wizard
- Local Config
- Logs / Details

## Radio Setup Wizard

The wizard searches the local JSON catalog at `web/system_catalog.example.json` using state, county, and city. It then filters talkgroups by category and saves a local runtime config through the existing backend API.

The starter catalog is intentionally small. It is a scaffold for verified local systems or a future licensed/imported data source. Replace placeholder talkgroups with verified clear talkgroups before field use.

## Data source rule

Do not hard-code a national radio database into the app. The wizard should load from a local/imported catalog so licensing, data accuracy, and offline use can be handled cleanly.
