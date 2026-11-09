# Changelog

All notable changes to this project are documented here.

## [1.1.0] - 2026-11-09

### Added
- **Native `NSMenu` Architecture**: Embedded the dashboard inside a native `NSMenuItem`. macOS WindowServer now composites the 100% authentic system selection pill highlight with native squircle corner curvature, floating insets, and vibrancy blur, matching all system and third-party menu bar items.
- **Fixed-Width Anti-Jitter Display**: Menu bar item uses a constant 4-character slot (`MENUBAR_DIGITS = 4`) with right-aligned numbers so surrounding menu bar icons never jump or shift when throughput increases or decreases.
- **Common RunLoop Mode Telemetry**: Network polling and speed test updates now run in `NSRunLoopCommonModes`, ensuring live throughput and speed test updates continue without pausing while the menu is open.

### Changed
- **Optimized Menu Bar Spacing & Typography**: Adjusted font size to 8.0 pt and line height to 8.5 pt to provide ~3.5 pt of comfortable vertical padding at the top and bottom of the menu bar, eliminating edge-to-edge crowding.
- Replaced custom floating `NSPanel` and manual window positioning with native macOS menu window positioning and automatic dismissals.

### Choosing Between Versions
| Feature / Behavior | v1.1.0 (Latest / Recommended) | v1.0.0 (Legacy) |
|---|---|---|
| **Highlight Style** | 100% Native macOS system highlight (exact Apple roundness & vibrancy) | Custom layer highlight (`CALayer`) with fixed 7pt radius |
| **Menu Bar Width** | Fixed-width (anti-jitter; icons stay stationary) | Variable-width (resizes dynamically with number of digits) |
| **Window Architecture** | Native `NSMenu` / `NSMenuItem` container | Custom `NSPanel` (`GlassPanel`) floating window |
| **Vertical Padding** | ~3.5 pt top & bottom breathing room | Edge-to-edge (~0.5–1.0 pt margin) |
| **Dismissal Behavior** | Native macOS menu outside-click dismissal | Custom global mouse event tracking monitor |

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