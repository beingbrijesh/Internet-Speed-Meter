#!/usr/bin/env python3
"""Speed Meter - macOS menu-bar network monitor.

Run:  python speed_meter.py            (foreground, Ctrl+C to stop)
      python speed_meter.py --detach   (background)
Quit: panel Quit button, right-click the menu bar item, or: pkill -f speed_meter.py
Deps: pip install rumps psutil pyobjc-framework-Cocoa certifi
"""

import os
import sys

if "--detach" in sys.argv:
    import subprocess

    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    print("Speed Meter started in the background "
          "(right-click its menu bar item -> Quit, or run: pkill -f speed_meter.py)")
    sys.exit(0)

import fcntl
import gc
import math
import tempfile
import threading
import time
import warnings
from collections import deque

import objc
import psutil
import rumps
from AppKit import (
    NSAnimationContext,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSPanel,
    NSParagraphStyleAttributeName,
    NSScreen,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
    NSVisualEffectView,
    NSWorkspace,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)
from Foundation import (
    NSDictionary,
    NSMakePoint,
    NSMakeRect,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSString,
    NSUserDefaults,
)
from PyObjCTools import AppHelper

if hasattr(objc, "ObjCPointerWarning"):
    # harmless, but each warning text embeds a unique address that Python would keep forever
    warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
MENUBAR_FONT_SIZE = 8.0      # numbers (monospaced)
ARROW_FONT_SIZE = 5.5        # arrow glyphs
MENUBAR_LINE_HEIGHT = 8.5    # distance between lines (leaves ~3.5 pt top & bottom padding)
MENUBAR_GAP = 2.0            # space between arrow and number
MENUBAR_PAD = 6.0            # left/right padding inside the menu bar item
MENUBAR_DIGITS = 4           # fixed character width (e.g. "999K", "1.2M") to prevent size jitter
TICK_TOLERANCE = 0.3         # timer slack (s) so macOS can coalesce wakeups

PANEL_W, PANEL_H = 360, 262
AREA = NSMakeRect(16, 62, 328, 120)
TEST_DURATION = 6.0
PARALLEL_STREAMS = 4
RESULT_HOLD_SECONDS = 12
HISTORY_LEN = 60
PREF_KEY = "panelStyle"

HOST = "speed.cloudflare.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) SpeedMeter/1.0", "Accept": "*/*"}
DOWN_CHUNK = 25_000_000
UP_CHUNK = 8_000_000

IMAGE_ONLY = 1


def _make_ssl_ctx():
    """Built only while a speed test runs, then dropped."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def fmt_short(bytes_per_sec):
    kb = bytes_per_sec / 1024
    if kb < 1000:
        return f"{int(kb)}K"
    mb = kb / 1024
    return f"{mb:.1f}M" if mb < 10 else f"{int(mb)}M"


def fmt_mbps(v):
    if v < 10:
        return f"{v:.2f}"
    if v < 100:
        return f"{v:.1f}"
    return f"{v:.0f}"


def gauge_frac(mbps):
    return min(math.log10(1 + max(mbps, 0)) / math.log10(1001), 1.0)


def metric_specs():
    return [("DOWNLOAD", "Mbps", NSColor.systemBlueColor()),
            ("UPLOAD", "Mbps", NSColor.systemGreenColor()),
            ("LATENCY", "ms", NSColor.systemOrangeColor())]


def curve(layer):
    try:
        layer.setCornerCurve_("continuous")
    except Exception:
        pass


def make_label(text, size, weight=NSFontWeightRegular, color=None,
               align=NSTextAlignmentLeft, mono=False, frame=None):
    f = NSTextField.labelWithString_(text)
    if mono:
        f.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight))
    else:
        f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    f.setTextColor_(color or NSColor.labelColor())
    f.setAlignment_(align)
    if frame is not None:
        f.setFrame_(frame)
    return f


def style_glass(layer, radius, fill=0.06, border=0.14, border_w=0.5):
    layer.setCornerRadius_(radius)
    curve(layer)
    layer.setBackgroundColor_(NSColor.labelColor().colorWithAlphaComponent_(fill).CGColor())
    layer.setBorderWidth_(border_w)
    layer.setBorderColor_(NSColor.labelColor().colorWithAlphaComponent_(border).CGColor())


def set_button_title(btn, title, fg):
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(NSTextAlignmentCenter)
    attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold),
        NSForegroundColorAttributeName: fg,
        NSParagraphStyleAttributeName: para,
    }
    btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(title, attrs))


def style_primary(btn, enabled=True):
    lay = btn.layer()
    accent = NSColor.controlAccentColor()
    lay.setBackgroundColor_(accent.colorWithAlphaComponent_(0.88 if enabled else 0.42).CGColor())
    lay.setBorderWidth_(0.75)
    lay.setBorderColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.30).CGColor())
    lay.setShadowColor_(accent.CGColor())
    lay.setShadowOpacity_(0.35 if enabled else 0.0)
    lay.setShadowRadius_(8)
    lay.setShadowOffset_((0, -2))


# --------------------------------------------------------------------------
# Custom views
# --------------------------------------------------------------------------
class SegmentedView(NSView):
    """Glass capsule segmented control with a sliding pill (no focus ring)."""

    def acceptsFirstMouse_(self, event):
        return True

    def acceptsFirstResponder(self):
        return False

    def hitTest_(self, point):
        sup = self.superview()
        if sup is None:
            return None
        p = self.convertPoint_fromView_(point, sup)
        b = self.bounds()
        if 0 <= p.x <= b.size.width and 0 <= p.y <= b.size.height:
            return self
        return None

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        n = len(self.seg_names)
        idx = max(0, min(n - 1, int(p.x / (self.bounds().size.width / n))))
        if idx != self.seg_selected:
            self.select(idx, True)
            self.on_select(idx)

    @objc.python_method
    def configure(self, names, selected, on_select):
        self.seg_names = list(names)
        self.on_select = on_select
        b = self.bounds()
        w, h = b.size.width, b.size.height
        self.seg_w = w / len(names)
        self.seg_inset = 3.0
        self.setWantsLayer_(True)
        style_glass(self.layer(), h / 2, fill=0.07, border=0.12)

        self.pill = NSView.alloc().initWithFrame_(self._pill_frame(selected))
        self.pill.setWantsLayer_(True)
        pl = self.pill.layer()
        style_glass(pl, (h - 2 * self.seg_inset) / 2, fill=0.16, border=0.20)
        pl.setShadowColor_(NSColor.blackColor().CGColor())
        pl.setShadowOpacity_(0.20)
        pl.setShadowRadius_(3)
        pl.setShadowOffset_((0, -1))
        self.addSubview_(self.pill)

        self.seg_labels = []
        for i, name in enumerate(names):
            lab = make_label(name, 12, NSFontWeightMedium, NSColor.secondaryLabelColor(),
                             NSTextAlignmentCenter,
                             frame=NSMakeRect(i * self.seg_w, (h - 15) / 2, self.seg_w, 15))
            self.addSubview_(lab)
            self.seg_labels.append(lab)
        self.seg_selected = selected
        self._recolor()

    @objc.python_method
    def _pill_frame(self, idx):
        h = self.bounds().size.height
        return NSMakeRect(idx * self.seg_w + self.seg_inset, self.seg_inset,
                          self.seg_w - 2 * self.seg_inset, h - 2 * self.seg_inset)

    @objc.python_method
    def _recolor(self):
        for i, lab in enumerate(self.seg_labels):
            lab.setTextColor_(NSColor.labelColor() if i == self.seg_selected
                              else NSColor.secondaryLabelColor())

    @objc.python_method
    def select(self, idx, animated=True):
        self.seg_selected = idx
        self._recolor()
        if animated:
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.22)
            self.pill.animator().setFrame_(self._pill_frame(idx))
            NSAnimationContext.endGrouping()
        else:
            self.pill.setFrame_(self._pill_frame(idx))


class MenuBarTextView(NSView):
    """Two-line readout drawn from pre-built attributed strings at pre-computed points,
    so nothing is allocated per draw. Transparent to clicks."""

    def isOpaque(self):
        return False

    def hitTest_(self, point):
        return None

    @objc.python_method
    def set_text(self, down_as, up_as, down_len=0, up_len=0):
        self.mb_lines = (self.mb_arrow_dn, down_as, self.mb_arrow_up, up_as)
        self.mb_lens = (down_len, up_len)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def mb_compute_points(self, h):
        cfg = self.mb_cfg
        line_h = MENUBAR_LINE_HEIGHT
        y0 = (h - 2 * line_h) / 2
        y_down, y_up = y0 + line_h, y0
        self.mb_pts_h = h
        self.mb_y_down_arr = y_down + (line_h - cfg["arrow_h"]["▼"]) / 2
        self.mb_y_down_num = y_down + (line_h - cfg["num_h"]) / 2
        self.mb_y_up_arr = y_up + (line_h - cfg["arrow_h"]["▲"]) / 2
        self.mb_y_up_num = y_up + (line_h - cfg["num_h"]) / 2

    def drawRect_(self, rect):
        lines = getattr(self, "mb_lines", None)
        if lines is None:
            return
        h = self.bounds().size.height
        if getattr(self, "mb_pts_h", None) != h:
            self.mb_compute_points(h)
        cfg = self.mb_cfg
        arrow_dn, down_as, arrow_up, up_as = lines
        down_len, up_len = getattr(self, "mb_lens", (0, 0))
        num_x = MENUBAR_PAD + cfg["arrow_w"] + MENUBAR_GAP
        char_w = cfg["char_w"]
        max_d = cfg["max_digits"]

        x_down = num_x + max(0, max_d - down_len) * char_w
        x_up = num_x + max(0, max_d - up_len) * char_w

        arrow_dn.drawAtPoint_(NSMakePoint(MENUBAR_PAD, self.mb_y_down_arr))
        down_as.drawAtPoint_(NSMakePoint(x_down, self.mb_y_down_num))
        arrow_up.drawAtPoint_(NSMakePoint(MENUBAR_PAD, self.mb_y_up_arr))
        up_as.drawAtPoint_(NSMakePoint(x_up, self.mb_y_up_num))


class RingView(NSView):
    """270-degree arc gauge. Set .ring_frac (0..1) and .ring_color."""

    def drawRect_(self, rect):
        b = self.bounds()
        center = NSMakePoint(b.size.width / 2, b.size.height / 2)
        r = min(b.size.width, b.size.height) / 2 - 5

        track = NSBezierPath.bezierPath()
        track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            center, r, 225, -45, True)
        track.setLineWidth_(7)
        track.setLineCapStyle_(1)
        NSColor.labelColor().colorWithAlphaComponent_(0.12).setStroke()
        track.stroke()

        frac = max(0.0, min(1.0, getattr(self, "ring_frac", 0.0)))
        if frac > 0.02:
            arc = NSBezierPath.bezierPath()
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                center, r, 225, 225 - 270 * frac, True)
            arc.setLineWidth_(7)
            arc.setLineCapStyle_(1)
            getattr(self, "ring_color", NSColor.systemBlueColor()).setStroke()
            arc.stroke()


_PEAK_ATTRS = None
_PEAK_CACHE = {}


def peak_label(text):
    """Attributed string for the Graph 'peak' label; attributes converted once, strings reused."""
    global _PEAK_ATTRS
    if _PEAK_ATTRS is None:
        _PEAK_ATTRS = NSDictionary.dictionaryWithDictionary_({
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(9, NSFontWeightMedium),
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor()})
    a = _PEAK_CACHE.get(text)
    if a is None:
        if len(_PEAK_CACHE) >= 128:
            _PEAK_CACHE.pop(next(iter(_PEAK_CACHE)))
        a = NSAttributedString.alloc().initWithString_attributes_(text, _PEAK_ATTRS)
        _PEAK_CACHE[text] = a
    return a


class GraphView(NSView):
    """Scrolling download/upload history. Set .hist = [(down, up), ...]."""

    def drawRect_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        clip = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 16, 16)
        NSColor.labelColor().colorWithAlphaComponent_(0.06).setFill()
        clip.fill()
        NSColor.labelColor().colorWithAlphaComponent_(0.14).setStroke()
        clip.setLineWidth_(0.5)
        clip.stroke()

        hist = getattr(self, "hist", None) or []
        if len(hist) < 2:
            return
        peak = max(max(d for d, _ in hist), max(u for _, u in hist), 1.0)
        top = peak * 1.25
        step = w / (HISTORY_LEN - 1)
        n = len(hist)

        NSGraphicsContext.saveGraphicsState()
        clip.addClip()
        for idx, color in ((1, NSColor.systemGreenColor()), (0, NSColor.systemBlueColor())):
            pts = [(w - (n - 1 - i) * step, 6 + (hist[i][idx] / top) * (h - 14))
                   for i in range(n)]
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(*pts[0]))
            for p in pts[1:]:
                line.lineToPoint_(NSMakePoint(*p))
            fill = line.copy()
            fill.lineToPoint_(NSMakePoint(pts[-1][0], 0))
            fill.lineToPoint_(NSMakePoint(pts[0][0], 0))
            fill.closePath()
            color.colorWithAlphaComponent_(0.18).setFill()
            fill.fill()
            color.setStroke()
            line.setLineWidth_(1.5)
            line.setLineJoinStyle_(1)
            line.stroke()
        NSGraphicsContext.restoreGraphicsState()

        peak_label(f"peak {fmt_mbps(peak)} Mbps").drawAtPoint_(NSMakePoint(10, h - 15))


# --------------------------------------------------------------------------
# Meter styles (all share: root, wants_history, set_metric(i, text, frac), set_history(h))
# --------------------------------------------------------------------------
class MetricCard:
    def __init__(self, x, y, w, h, caption, unit, accent):
        self.view = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        self.view.setWantsLayer_(True)
        style_glass(self.view.layer(), 18, fill=0.06, border=0.14)
        self.view.addSubview_(make_label(
            caption, 10, NSFontWeightSemibold, NSColor.secondaryLabelColor(),
            NSTextAlignmentCenter, frame=NSMakeRect(0, h - 26, w, 14)))
        self.value = make_label(
            "—", 28, NSFontWeightSemibold, accent, NSTextAlignmentCenter,
            mono=True, frame=NSMakeRect(0, 52, w, 36))
        self.view.addSubview_(self.value)
        self.view.addSubview_(make_label(
            unit, 11, NSFontWeightMedium, NSColor.secondaryLabelColor(),
            NSTextAlignmentCenter, frame=NSMakeRect(0, 34, w, 14)))
        self.track_w = w - 28
        track = NSView.alloc().initWithFrame_(NSMakeRect(14, 16, self.track_w, 4))
        track.setWantsLayer_(True)
        track.layer().setCornerRadius_(2)
        track.layer().setBackgroundColor_(
            NSColor.labelColor().colorWithAlphaComponent_(0.12).CGColor())
        self.view.addSubview_(track)
        self.fill = NSView.alloc().initWithFrame_(NSMakeRect(14, 16, 0, 4))
        self.fill.setWantsLayer_(True)
        self.fill.layer().setCornerRadius_(2)
        self.fill.layer().setBackgroundColor_(accent.CGColor())
        self.view.addSubview_(self.fill)

    def set(self, text, frac):
        self.value.setStringValue_(text)
        frac = max(0.0, min(1.0, frac))
        self.fill.setFrame_(NSMakeRect(14, 16, self.track_w * frac, 4))


class BarsStyle:
    wants_history = False

    def __init__(self):
        self.root = NSView.alloc().initWithFrame_(AREA)
        self.cards = []
        for i, (cap, unit, col) in enumerate(metric_specs()):
            c = MetricCard(i * 112, 0, 104, 120, cap, unit, col)
            self.root.addSubview_(c.view)
            self.cards.append(c)

    def set_metric(self, i, text, frac):
        self.cards[i].set(text, frac)

    def set_history(self, hist):
        pass


class GaugeStyle:
    wants_history = False

    def __init__(self):
        self.root = NSView.alloc().initWithFrame_(AREA)
        self.rings, self.values = [], []
        for i, (cap, unit, col) in enumerate(metric_specs()):
            x = i * 112
            ring = RingView.alloc().initWithFrame_(NSMakeRect(x + 14, 30, 76, 76))
            ring.ring_color = col
            ring.ring_frac = 0.0
            self.root.addSubview_(ring)
            self.rings.append(ring)
            val = make_label("—", 18, NSFontWeightSemibold, col, NSTextAlignmentCenter,
                             mono=True, frame=NSMakeRect(x + 14, 62, 76, 24))
            self.root.addSubview_(val)
            self.values.append(val)
            self.root.addSubview_(make_label(
                unit, 10, NSFontWeightMedium, NSColor.secondaryLabelColor(),
                NSTextAlignmentCenter, frame=NSMakeRect(x + 14, 48, 76, 12)))
            self.root.addSubview_(make_label(
                cap, 10, NSFontWeightSemibold, NSColor.secondaryLabelColor(),
                NSTextAlignmentCenter, frame=NSMakeRect(x, 6, 104, 14)))

    def set_metric(self, i, text, frac):
        self.values[i].setStringValue_(text)
        self.rings[i].ring_frac = frac
        self.rings[i].setNeedsDisplay_(True)

    def set_history(self, hist):
        pass


class GraphStyle:
    wants_history = True

    def __init__(self):
        self.root = NSView.alloc().initWithFrame_(AREA)
        self.values = []
        for i, (cap, unit, col) in enumerate(metric_specs()):
            x = i * 112
            self.root.addSubview_(make_label(
                f"{cap} · {unit}", 9, NSFontWeightSemibold, NSColor.secondaryLabelColor(),
                NSTextAlignmentCenter, frame=NSMakeRect(x, 106, 104, 12)))
            val = make_label("—", 17, NSFontWeightSemibold, col, NSTextAlignmentCenter,
                             mono=True, frame=NSMakeRect(x, 82, 104, 22))
            self.root.addSubview_(val)
            self.values.append(val)
        self.graph = GraphView.alloc().initWithFrame_(NSMakeRect(0, 0, 328, 76))
        self.graph.hist = []
        self.root.addSubview_(self.graph)

    def set_metric(self, i, text, frac):
        self.values[i].setStringValue_(text)

    def set_history(self, hist):
        self.graph.hist = hist
        self.graph.setNeedsDisplay_(True)


STYLES = [("Bars", BarsStyle), ("Gauge", GaugeStyle), ("Graph", GraphStyle)]


# --------------------------------------------------------------------------
# Objective-C plumbing
# --------------------------------------------------------------------------
class Controller(NSObject):
    def initWithApp_(self, app):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def menuWillOpen_(self, menu):
        self._app.on_menu_open()

    def menuDidClose_(self, menu):
        self._app.on_menu_close()

    def runTest_(self, sender):
        self._app.start_test()

    def quit_(self, sender):
        rumps.quit_application()

    def screensSleep_(self, note):
        self._app._pause()

    def screensWake_(self, note):
        self._app._resume()


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------
class SpeedMeter(rumps.App):
    def __init__(self):
        super().__init__("Speed Meter", title="", quit_button=None)
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)

        net = psutil.net_io_counters()
        self._last_sent, self._last_recv = net.bytes_sent, net.bytes_recv
        self._last_t = time.monotonic()
        self._down_mbps = 0.0
        self._up_mbps = 0.0
        self._last_title = None
        self.history = deque(maxlen=HISTORY_LEN)

        self.controller = Controller.alloc().initWithApp_(self)
        self._status_item = None
        self._nsmenu = None
        self._nsmenu_item = None
        self.container = None
        self.style = None
        self.status_label = None
        self.run_btn = None
        self.panel_visible = False

        idx = int(NSUserDefaults.standardUserDefaults().integerForKey_(PREF_KEY))
        self.style_idx = idx if 0 <= idx < len(STYLES) else 0
        self.metrics = [("—", 0.0)] * 3

        self.mode = "live"              # live | testing | result
        self.hold_until = 0.0
        self._status = ("Live", None)   # remembered so the panel can be rebuilt
        self._btn_state = "idle"        # idle | testing | again
        self._mb_view = None
        self._mb_length = None
        self._ssl_ctx = None

        self._boot = rumps.Timer(self._bootstrap, 0.2)
        self._boot.start()
        self._tick = rumps.Timer(self.tick, 1)
        self._start_tick()

    # ---- status item ------------------------------------------------------
    def _bootstrap(self, timer):
        timer.stop()
        self._status_item = self._nsapp.nsstatusitem
        btn = self._status_item.button()
        btn.setTitle_("")
        btn.setImagePosition_(IMAGE_ONLY)

        self._init_menubar_metrics()
        self._set_menubar("0K", "0K")

        # Approach B: Native NSMenu with Speed Meter dashboard embedded in an NSMenuItem
        self._nsmenu = NSMenu.alloc().init()
        self._nsmenu.setDelegate_(self.controller)
        self._nsmenu_item = NSMenuItem.alloc().init()
        self._build_panel()
        self._nsmenu_item.setView_(self.container)
        self._nsmenu.addItem_(self._nsmenu_item)
        self._status_item.setMenu_(self._nsmenu)

        nc = NSWorkspace.sharedWorkspace().notificationCenter()   # pause timer while display sleeps
        nc.addObserver_selector_name_object_(
            self.controller, "screensSleep:", "NSWorkspaceScreensDidSleepNotification", None)
        nc.addObserver_selector_name_object_(
            self.controller, "screensWake:", "NSWorkspaceScreensDidWakeNotification", None)

    def _start_tick(self):
        self._tick.start()
        t = getattr(self._tick, "_nstimer", None)
        if t is not None:
            try:
                t.setTolerance_(TICK_TOLERANCE)
                NSRunLoop.currentRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
            except Exception:
                pass

    def _pause(self):
        self._tick.stop()

    def _resume(self):
        net = psutil.net_io_counters()
        self._last_sent, self._last_recv = net.bytes_sent, net.bytes_recv
        self._last_t = time.monotonic()
        self._start_tick()

    def _init_menubar_metrics(self):
        label = NSColor.labelColor()
        num_attrs = {
            NSFontAttributeName: NSFont.monospacedSystemFontOfSize_weight_(
                MENUBAR_FONT_SIZE, NSFontWeightMedium),
            NSForegroundColorAttributeName: label,
        }
        arr_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(
                ARROW_FONT_SIZE, NSFontWeightRegular),
            NSForegroundColorAttributeName: label,
        }
        zero = NSString.stringWithString_("0").sizeWithAttributes_(num_attrs)
        d = NSString.stringWithString_("▼").sizeWithAttributes_(arr_attrs)
        u = NSString.stringWithString_("▲").sizeWithAttributes_(arr_attrs)
        self._mb_char_w = zero.width          # monospaced: every glyph the same width
        self._mb_arrow_w = max(d.width, u.width)
        self._mb_length = math.ceil(
            self._mb_arrow_w + MENUBAR_GAP + MENUBAR_DIGITS * self._mb_char_w
        ) + 2 * MENUBAR_PAD

        self._mb_num_ns = NSDictionary.dictionaryWithDictionary_(num_attrs)
        arr_ns = NSDictionary.dictionaryWithDictionary_(arr_attrs)
        self._mb_as_cache = {}

        self._status_item.setLength_(self._mb_length)
        btn = self._status_item.button()
        view = MenuBarTextView.alloc().initWithFrame_(btn.bounds())
        view.setAutoresizingMask_(18)         # width + height sizable
        view.mb_cfg = {
            "num_h": zero.height, "arrow_h": {"▼": d.height, "▲": u.height},
            "arrow_w": self._mb_arrow_w,
            "char_w": self._mb_char_w,
            "max_digits": MENUBAR_DIGITS,
        }
        view.mb_arrow_dn = NSAttributedString.alloc().initWithString_attributes_("▼", arr_ns)
        view.mb_arrow_up = NSAttributedString.alloc().initWithString_attributes_("▲", arr_ns)
        view.mb_lines = None
        btn.addSubview_(view)
        self._mb_view = view

    def _mb_attr(self, text):
        a = self._mb_as_cache.get(text)
        if a is None:
            if len(self._mb_as_cache) >= 256:
                self._mb_as_cache.pop(next(iter(self._mb_as_cache)))
            a = NSAttributedString.alloc().initWithString_attributes_(text, self._mb_num_ns)
            self._mb_as_cache[text] = a
        return a

    def _set_menubar(self, down, up):
        key = (down, up)
        if key == self._last_title or self._status_item is None or self._mb_view is None:
            return
        self._last_title = key
        self._mb_view.set_text(self._mb_attr(down), self._mb_attr(up), len(down), len(up))

    def on_menu_open(self):
        self.panel_visible = True
        self._refresh_panel()

    def on_menu_close(self):
        self.panel_visible = False

    # ---- 1 Hz telemetry ----------------------------------------------------
    def tick(self, _):
        with objc.autorelease_pool():
            self._tick_body()

    def _tick_body(self):
        now = time.monotonic()
        dt = now - self._last_t
        if dt <= 0:
            return
        net = psutil.net_io_counters()
        d_recv = max(0, net.bytes_recv - self._last_recv)
        d_sent = max(0, net.bytes_sent - self._last_sent)
        self._last_recv, self._last_sent, self._last_t = net.bytes_recv, net.bytes_sent, now

        self._down_mbps = d_recv / dt * 8 / 1e6
        self._up_mbps = d_sent / dt * 8 / 1e6
        self.history.append((self._down_mbps, self._up_mbps))
        self._set_menubar(fmt_short(d_recv / dt), fmt_short(d_sent / dt))
        self._refresh_panel()

    def _refresh_panel(self):
        if not self.panel_visible or self.style is None:
            return
        if self.mode == "result" and time.time() >= self.hold_until:
            self.mode = "live"
            self.ui_status("Live", NSColor.systemGreenColor())
        if self.mode == "live":
            self.ui_card(0, fmt_mbps(self._down_mbps), gauge_frac(self._down_mbps))
            self.ui_card(1, fmt_mbps(self._up_mbps), gauge_frac(self._up_mbps))
        if self.style.wants_history:
            self.style.set_history(list(self.history))

    # ---- panel ------------------------------------------------------------
    def _build_panel(self):
        rect = NSMakeRect(0, 0, PANEL_W, PANEL_H)
        container = NSView.alloc().initWithFrame_(rect)
        self.container = container

        container.addSubview_(make_label(
            "Network Speed", 13, NSFontWeightSemibold,
            frame=NSMakeRect(18, PANEL_H - 38, 200, 18)))
        self.status_label = make_label(
            "● Live", 11, NSFontWeightMedium, NSColor.systemGreenColor(),
            NSTextAlignmentRight, frame=NSMakeRect(PANEL_W - 18 - 150, PANEL_H - 36, 150, 14))
        container.addSubview_(self.status_label)

        seg = SegmentedView.alloc().initWithFrame_(NSMakeRect(16, 188, 328, 26))
        seg.configure([n for n, _ in STYLES], self.style_idx, self.set_style)
        container.addSubview_(seg)

        pad = 16
        self.run_btn = self._make_button(
            NSMakeRect(pad, 16, PANEL_W - 2 * pad - 72, 32), "runTest:")
        style_primary(self.run_btn, True)
        set_button_title(self.run_btn, "Run Full Test", NSColor.whiteColor())
        quit_btn = self._make_button(
            NSMakeRect(PANEL_W - pad - 64, 16, 64, 32), "quit:")
        style_glass(quit_btn.layer(), 16, fill=0.09, border=0.16)
        set_button_title(quit_btn, "Quit", NSColor.labelColor())
        container.addSubview_(self.run_btn)
        container.addSubview_(quit_btn)

        self._apply_style(self.style_idx)
        self.ui_status(*self._status)
        self._apply_btn()
        return container

    def _make_button(self, frame, action):
        b = NSButton.alloc().initWithFrame_(frame)
        b.setBordered_(False)
        b.setFocusRingType_(1)
        b.setWantsLayer_(True)
        lay = b.layer()
        lay.setCornerRadius_(frame.size.height / 2)
        curve(lay)
        b.setTarget_(self.controller)
        b.setAction_(action)
        return b

    def _apply_style(self, idx):
        if self.style is not None:
            self.style.root.removeFromSuperview()
        self.style = STYLES[idx][1]()
        self.container.addSubview_(self.style.root)
        for i, (text, frac) in enumerate(self.metrics):
            self.style.set_metric(i, text, frac)
        self.style.set_history(list(self.history))

    def set_style(self, idx):
        if not 0 <= idx < len(STYLES):
            return
        self.style_idx = idx
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(idx, PREF_KEY)
        self._apply_style(idx)

    # ---- UI mutators (main thread only) -----------------------------------
    def ui_status(self, text, color):
        self._status = (text, color)
        if self.status_label is not None:
            self.status_label.setStringValue_("● " + text)
            self.status_label.setTextColor_(color or NSColor.systemGreenColor())

    def ui_card(self, idx, text, frac):
        if self.metrics[idx] == (text, frac):
            return
        self.metrics[idx] = (text, frac)
        if self.style is not None:
            self.style.set_metric(idx, text, frac)

    def _apply_btn(self):
        btn = self.run_btn
        if btn is None:
            return
        if self._btn_state == "testing":
            btn.setEnabled_(False)
            style_primary(btn, False)
            set_button_title(btn, "Testing…", NSColor.whiteColor())
        else:
            btn.setEnabled_(True)
            style_primary(btn, True)
            set_button_title(btn, "Run Again" if self._btn_state == "again"
                             else "Run Full Test", NSColor.whiteColor())

    def ui_test_started(self):
        for i in range(3):
            self.ui_card(i, "—", 0.0)
        self._btn_state = "testing"
        self._apply_btn()

    def ui_test_finished(self, ok):
        self.mode = "result" if ok else "live"
        self.hold_until = time.time() + RESULT_HOLD_SECONDS
        if ok:
            self.ui_status("Complete", NSColor.systemGreenColor())
        self._btn_state = "again"
        self._apply_btn()
        gc.collect()

    # ---- speed test -------------------------------------------------------
    def start_test(self):
        if self.mode == "testing":
            return
        self.mode = "testing"
        self.ui_test_started()
        self.ui_status("Measuring latency", NSColor.systemOrangeColor())
        threading.Thread(target=self._run_test, daemon=True).start()

    def _run_test(self):
        with objc.autorelease_pool():
            self._run_test_body()

    def _run_test_body(self):
        ui = AppHelper.callAfter
        ok = False
        try:
            self._ssl_ctx = _make_ssl_ctx()
            latency = self._measure_latency()
            ui(self.ui_card, 2, f"{latency:.0f}", 1 - min(latency / 200.0, 1.0))

            ui(self.ui_status, "Testing download", NSColor.systemBlueColor())
            down = self._transfer(
                "down", lambda v: ui(self.ui_card, 0, fmt_mbps(v), gauge_frac(v)))
            ui(self.ui_card, 0, fmt_mbps(down), gauge_frac(down))

            ui(self.ui_status, "Testing upload", NSColor.systemGreenColor())
            up = self._transfer(
                "up", lambda v: ui(self.ui_card, 1, fmt_mbps(v), gauge_frac(v)))
            ui(self.ui_card, 1, fmt_mbps(up), gauge_frac(up))
            ok = True
        except Exception:
            ui(self.ui_status, "Test failed – check connection", NSColor.systemRedColor())
        finally:
            self._ssl_ctx = None
            ui(self.ui_test_finished, ok)

    def _new_conn(self):
        import http.client

        return http.client.HTTPSConnection(HOST, timeout=6, context=self._ssl_ctx)

    def _measure_latency(self):
        conn = self._new_conn()
        samples = []
        try:
            for i in range(8):
                t0 = time.perf_counter()
                conn.request("GET", "/__down?bytes=0", headers=HEADERS)
                conn.getresponse().read()
                ms = (time.perf_counter() - t0) * 1000
                if i == 0:
                    continue
                samples.append(ms)
                AppHelper.callAfter(
                    self.ui_card, 2, f"{ms:.0f}", 1 - min(ms / 200.0, 1.0))
        finally:
            conn.close()
        samples.sort()
        n = len(samples)
        return (samples[n // 2] + samples[(n - 1) // 2]) / 2

    def _transfer(self, direction, on_progress):
        counter = [0]
        lock = threading.Lock()
        stop = threading.Event()

        def add(n):
            with lock:
                counter[0] += n

        def down_worker():
            conn = self._new_conn()
            view = memoryview(bytearray(65536))   # one reusable buffer
            try:
                while not stop.is_set():
                    conn.request("GET", f"/__down?bytes={DOWN_CHUNK}", headers=HEADERS)
                    resp = conn.getresponse()
                    while not stop.is_set():
                        n = resp.readinto(view)
                        if not n:
                            break
                        add(n)
            except Exception:
                pass
            finally:
                conn.close()

        payload = b"\0" * 65536

        def up_worker():
            conn = self._new_conn()
            hdrs = dict(HEADERS)
            hdrs["Content-Type"] = "application/octet-stream"
            hdrs["Content-Length"] = str(UP_CHUNK)

            def body():
                sent = 0
                while sent < UP_CHUNK and not stop.is_set():
                    yield payload
                    add(len(payload))
                    sent += len(payload)

            try:
                while not stop.is_set():
                    conn.request("POST", "/__up", body=body(), headers=hdrs)
                    if stop.is_set():
                        break
                    conn.getresponse().read()
            except Exception:
                pass
            finally:
                conn.close()

        worker = down_worker if direction == "down" else up_worker
        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(PARALLEL_STREAMS)]
        for t in threads:
            t.start()

        t_start = last_t = time.perf_counter()
        last_b = 0
        ema = 0.0
        warm = None
        while True:
            time.sleep(0.12)
            now = time.perf_counter()
            with lock:
                total = counter[0]
            inst = (total - last_b) * 8 / max(now - last_t, 1e-6) / 1e6
            ema = inst if ema == 0 else 0.6 * ema + 0.4 * inst
            last_t, last_b = now, total
            elapsed = now - t_start
            if warm is None and elapsed >= 1.0:
                warm = (now, total)
            if elapsed >= TEST_DURATION:
                break
            with objc.autorelease_pool():
                on_progress(ema)

        stop.set()
        for t in threads:
            t.join(timeout=1.0)
        if warm and last_t > warm[0]:
            return (last_b - warm[1]) * 8 / (last_t - warm[0]) / 1e6
        return last_b * 8 / max(last_t - t_start, 1e-6) / 1e6


# --------------------------------------------------------------------------
def _acquire_single_instance():
    f = open(os.path.join(tempfile.gettempdir(), "speed_meter.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Speed Meter is already running.")
        sys.exit(0)
    return f


if __name__ == "__main__":
    _lock = _acquire_single_instance()
    SpeedMeter().run()