# Changelog

All notable changes to this project are documented here.

## [1.0.0] - 2026-10-14

### Added
- Live download and upload speed in the macOS menu bar, drawn as two stacked lines
- Liquid Glass dashboard panel (native glass on macOS 26+, blurred fallback on older versions)
- Three panel styles: Bars, Gauge, Graph (choice is remembered)
- Built-in speed test using Cloudflare: latency, download, upload with live updates
- Right-click Quit menu and single-instance lock
- `--detach` option to run the script in the background
- Optional standalone `.app` build via `build_app.sh` (Apple Silicon, ad-hoc signed, not notarized)

### Fixed
- Memory leak in the menu bar view's drawing code (350+ MB growth over hours)

### Changed
- Network and TLS modules load only when a speed test runs
- Panel is freed on close and rebuilt on open
- Idle footprint reduced to about 37 MB