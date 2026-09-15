# Changelog

All notable changes to this project are listed here.
Versions follow [Semantic Versioning](https://semver.org/):
MAJOR.MINOR.PATCH — new features bump MINOR, bug fixes bump PATCH.

## [1.0.0] - 2026-09-14

First release.

### Added
- Big PTT button that drives GPIO3 of a C-Media CM108/CM119 USB sound card.
- Momentary PTT (hold) or **Toggle mode** (click to latch TX on/off).
- Spacebar as PTT.
- **TX timeout** (seconds, 0 = off, default 120) with a countdown under the
  button; counts up when the timeout is off.
- **VoiceMeeter link**: the Mute button of the VoiceMeeter input strip using
  the CM108 (found automatically by name) acts as PTT (muted = transmitting),
  synced both ways with the app. Can be turned off.
- Settings saved in `%APPDATA%\CM108 PTT\settings.json`.
- Safety: PTT released at startup, on close, on mode/device change, and when a
  device or VoiceMeeter disappears.

[1.0.0]: https://github.com/alexduboiz/cm108-ptt-controller/releases/tag/v1.0.0
