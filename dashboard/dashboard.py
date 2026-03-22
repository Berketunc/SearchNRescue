"""
dashboard.py  –  High-Tech Telemetry Dashboard
Dark-themed PyQt6 dashboard matching the provided screenshots.
Connects to a serial port, parses telemetry, visualises instruments,
captures Anchor.py stdout, and logs everything in a styled terminal pane.
"""

import sys
import os
import math
import time
import csv
import shutil
import subprocess
import threading
import re
from datetime import datetime
import serial
import serial.tools.list_ports

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFrame, QSizePolicy,
    QTextEdit, QGraphicsDropShadowEffect, QScrollBar,
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, QSize,
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QRadialGradient, QLinearGradient, QPalette,
    QTextCursor,
)

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#050a14"
BG_PANEL    = "#080f1e"
BG_CARD     = "#0b1628"
ACCENT      = "#00f2ff"
ACCENT2     = "#00ff9d"
WARN        = "#ffaa00"
DANGER      = "#ff3860"
TEXT        = "#cbd5e1"
TEXT_DIM    = "#4a6080"
BORDER      = "#0d2035"
GRID_LINE   = "#0d2540"

def qc(hex_str):
    return QColor(hex_str)

# ── Fonts ─────────────────────────────────────────────────────────────────────
MONO = "Courier New"
SANS = "Segoe UI"

# ═══════════════════════════════════════════════════════════════════════════════
#  INSTRUMENT WIDGETS
# ═══════════════════════════════════════════════════════════════════════════════

class ArtificialHorizon(QWidget):
    """Attitude Indicator – pitch / roll."""

    def __init__(self):
        super().__init__()
        self.pitch = 0.0   # degrees, positive = nose up
        self.roll  = 0.0   # degrees, positive = right wing down
        self.has_data = False
        self.setMinimumSize(160, 160)

    def set_attitude(self, pitch, roll):
        self.pitch = pitch
        self.roll  = roll
        self.has_data = True
        self.update()

    def clear_data(self):
        self.pitch = 0.0
        self.roll = 0.0
        self.has_data = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 6
        cx, cy = w / 2, h / 2

        # clip to circle
        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), r, r)
        p.setClipPath(clip)

        # ── sky / ground ──────────────────────────────────────────────────
        p.save()
        p.translate(cx, cy)
        p.rotate(-self.roll)

        pitch_px = self.pitch * (r / 45)   # 45° fills half the dial

        sky_grad = QLinearGradient(0, -r, 0, pitch_px)
        sky_grad.setColorAt(0, QColor("#0a2a5e"))
        sky_grad.setColorAt(1, QColor("#1a4a8e"))
        p.fillRect(int(-r), int(-r), int(2*r), int(r + pitch_px), QBrush(sky_grad))

        gnd_grad = QLinearGradient(0, pitch_px, 0, r)
        gnd_grad.setColorAt(0, QColor("#3b1a08"))
        gnd_grad.setColorAt(1, QColor("#1a0a04"))
        p.fillRect(int(-r), int(pitch_px), int(2*r), int(r - pitch_px + 2), QBrush(gnd_grad))

        # horizon line
        pen = QPen(qc(ACCENT), 1.5)
        p.setPen(pen)
        p.drawLine(int(-r), int(pitch_px), int(r), int(pitch_px))

        # pitch ladders
        pen2 = QPen(Qt.GlobalColor.white, 0.8)
        p.setPen(pen2)
        p.setFont(QFont(MONO, 7))
        for deg in range(-30, 31, 10):
            if deg == 0:
                continue
            y = pitch_px - deg * (r / 45)
            ladder_w = r * 0.35 if deg % 20 == 0 else r * 0.2
            p.drawLine(int(-ladder_w), int(y), int(ladder_w), int(y))

        p.restore()

        # ── bezel ─────────────────────────────────────────────────────────
        p.setClipping(False)
        bezel_pen = QPen(qc(BORDER), 3)
        bezel_pen.setCosmetic(True)
        p.setPen(bezel_pen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # roll arc ticks
        tick_pen = QPen(Qt.GlobalColor.white, 1)
        p.setPen(tick_pen)
        for deg in [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]:
            angle_rad = math.radians(deg - 90)
            x1 = cx + (r - 8) * math.cos(angle_rad)
            y1 = cy + (r - 8) * math.sin(angle_rad)
            x2 = cx + r * math.cos(angle_rad)
            y2 = cy + r * math.sin(angle_rad)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # roll pointer
        p.save()
        p.translate(cx, cy)
        p.rotate(-self.roll)
        tri = QPainterPath()
        tri.moveTo(0, -(r - 12))
        tri.lineTo(-5, -(r - 4))
        tri.lineTo(5, -(r - 4))
        tri.closeSubpath()
        p.fillPath(tri, QBrush(qc(ACCENT)))
        p.restore()

        # centre dot
        p.setBrush(QBrush(Qt.GlobalColor.white))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 3, 3)

        if not self.has_data:
            p.setPen(QPen(qc(TEXT_DIM)))
            p.setFont(QFont(SANS, 8, QFont.Weight.Medium))
            p.drawText(
                QRectF(cx - r, cy - 8, 2 * r, 16),
                Qt.AlignmentFlag.AlignCenter,
                "Waiting for data",
            )


class CompassHSI(QWidget):
    """Heading / HSI compass."""

    def __init__(self):
        super().__init__()
        self.heading = 0.0
        self.setMinimumSize(160, 160)

    def set_heading(self, heading):
        self.heading = heading % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 6
        cx, cy = w / 2, h / 2

        # background
        grad = QRadialGradient(cx, cy, r)
        grad.setColorAt(0, QColor("#0b1628"))
        grad.setColorAt(1, QColor("#050a14"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(qc(BORDER), 2))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # rotating rose
        p.save()
        p.translate(cx, cy)
        p.rotate(-self.heading)

        cardinals = {0: "N", 90: "E", 180: "S", 270: "W"}
        for deg in range(0, 360, 5):
            angle_rad = math.radians(deg - 90)
            tick_len  = 12 if deg % 30 == 0 else (8 if deg % 10 == 0 else 5)
            col = qc(ACCENT) if deg % 90 == 0 else Qt.GlobalColor.white
            pen = QPen(col, 1.5 if deg % 90 == 0 else 0.8)
            p.setPen(pen)
            x1 = (r - tick_len) * math.cos(angle_rad)
            y1 = (r - tick_len) * math.sin(angle_rad)
            x2 = r * math.cos(angle_rad)
            y2 = r * math.sin(angle_rad)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

            if deg in cardinals:
                p.setFont(QFont(MONO, 9, QFont.Weight.Bold))
                p.setPen(QPen(qc(ACCENT)))
                lx = (r - 26) * math.cos(angle_rad)
                ly = (r - 26) * math.sin(angle_rad)
                p.drawText(QRectF(lx - 8, ly - 8, 16, 16),
                           Qt.AlignmentFlag.AlignCenter, cardinals[deg])
            elif deg % 30 == 0:
                p.setFont(QFont(MONO, 7))
                p.setPen(QPen(Qt.GlobalColor.white))
                lx = (r - 22) * math.cos(angle_rad)
                ly = (r - 22) * math.sin(angle_rad)
                p.drawText(QRectF(lx - 8, ly - 8, 16, 16),
                           Qt.AlignmentFlag.AlignCenter, str(deg // 10))
        p.restore()

        # fixed lubber line
        p.setPen(QPen(qc(ACCENT), 2))
        p.drawLine(int(cx), int(cy - r + 2), int(cx), int(cy - r + 16))

        # centre
        p.setBrush(QBrush(qc(ACCENT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4, 4)


class RadarNodeMap(QWidget):
    """Sweeping radar with detected node positions."""

    def __init__(self):
        super().__init__()
        self.sweep_angle = 0.0
        self.nodes = []       # list of (angle_deg, distance_fraction, label)
        self.blips = []       # [(x_frac, y_frac, age)]  age 0→1
        self.setMinimumSize(160, 160)

        self._sweep_timer = QTimer(self)
        self._sweep_timer.timeout.connect(self._advance_sweep)
        self._sweep_timer.start(30)

    def _advance_sweep(self):
        self.sweep_angle = (self.sweep_angle + 2) % 360
        # age blips
        self.blips = [(x, y, a + 0.01) for x, y, a in self.blips if a < 1.0]
        # generate new blip for each node near sweep
        for ang, dist, label in self.nodes:
            if abs((self.sweep_angle - ang) % 360) < 3:
                rad = math.radians(ang - 90)
                fx = 0.5 + dist * math.cos(rad) * 0.5
                fy = 0.5 + dist * math.sin(rad) * 0.5
                # avoid duplicates
                if not any(abs(x - fx) < 0.02 and abs(y - fy) < 0.02
                           for x, y, _ in self.blips):
                    self.blips.append((fx, fy, 0.0))
        self.update()

    def set_nodes(self, nodes):
        self.nodes = nodes
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 6
        cx, cy = w / 2, h / 2

        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), r, r)
        p.setClipPath(clip)

        # background
        bg_grad = QRadialGradient(cx, cy, r)
        bg_grad.setColorAt(0, QColor("#041508"))
        bg_grad.setColorAt(1, QColor("#020b04"))
        p.fillPath(clip, QBrush(bg_grad))

        # grid rings
        for i in range(1, 5):
            frac = i / 4
            ring_col = QColor(0, 180, 60, 60)
            p.setPen(QPen(ring_col, 0.8))
            p.drawEllipse(QPointF(cx, cy), r * frac, r * frac)

        # crosshairs
        p.setPen(QPen(QColor(0, 180, 60, 60), 0.8))
        p.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))
        p.drawLine(int(cx), int(cy - r), int(cx), int(cy + r))

        # sweep gradient
        sweep_rad = math.radians(self.sweep_angle - 90)
        for arc_offset in range(60, 0, -1):
            alpha = int(120 * (1 - arc_offset / 60))
            arc_col = QColor(0, 242, 100, alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(arc_col))
            path = QPainterPath()
            path.moveTo(cx, cy)
            start_angle = -(self.sweep_angle - arc_offset)
            path.arcTo(QRectF(cx - r, cy - r, 2*r, 2*r),
                       start_angle, -1)
            path.closeSubpath()
            p.drawPath(path)

        # sweep line
        ex = cx + r * math.cos(sweep_rad)
        ey = cy + r * math.sin(sweep_rad)
        sweep_pen = QPen(qc(ACCENT2), 2)
        sweep_pen.setCosmetic(True)
        p.setPen(sweep_pen)
        p.drawLine(int(cx), int(cy), int(ex), int(ey))

        # blips
        p.setClipping(False)
        for bx, by, age in self.blips:
            alpha = int(255 * (1 - age))
            size  = 6 * (1 - age * 0.5)
            blip_col = QColor(0, 255, 120, alpha)
            p.setBrush(QBrush(blip_col))
            p.setPen(Qt.PenStyle.NoPen)
            px = cx + (bx - 0.5) * 2 * r
            py = cy + (by - 0.5) * 2 * r
            p.drawEllipse(QPointF(px, py), size, size)

        # bezel
        p.setClipping(False)
        p.setPen(QPen(qc(BORDER), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)


class BarGraph(QWidget):
    """Thin vertical bar graph (altitude / signal)."""

    def __init__(self, label="", unit="", color=ACCENT, min_val=0, max_val=100):
        super().__init__()
        self.label   = label
        self.unit    = unit
        self.color   = color
        self.min_val = min_val
        self.max_val = max_val
        self.value   = 0.0
        self.setMinimumSize(50, 80)

    def set_value(self, v):
        self.value = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # background bar
        bar_x = w // 2 - 8
        bar_w = 16
        bar_h = h - 30
        bar_y = 8

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(GRID_LINE)))
        p.drawRect(bar_x, bar_y, bar_w, bar_h)

        # filled portion
        fraction = max(0, min(1, (self.value - self.min_val) /
                               (self.max_val - self.min_val)))
        fill_h = int(bar_h * fraction)
        grad = QLinearGradient(0, bar_y + bar_h - fill_h, 0, bar_y + bar_h)
        c = QColor(self.color)
        c2 = QColor(self.color)
        c2.setAlpha(120)
        grad.setColorAt(1, c)
        grad.setColorAt(0, c2)
        p.setBrush(QBrush(grad))
        p.drawRect(bar_x, bar_y + bar_h - fill_h, bar_w, fill_h)

        # border
        p.setPen(QPen(qc(BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bar_x, bar_y, bar_w, bar_h)

        # value label
        p.setFont(QFont(MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(qc(self.color)))
        val_str = f"{self.value:.1f} {self.unit}"
        p.drawText(QRectF(0, bar_y + bar_h + 2, w, 12),
                   Qt.AlignmentFlag.AlignCenter, val_str)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCELEROMETER READOUT
# ═══════════════════════════════════════════════════════════════════════════════

class AccelReadout(QWidget):
    def __init__(self):
        super().__init__()
        self.ax = None
        self.ay = None
        self.az = None
        self.has_data = False
        self._smooth_ax = None
        self._smooth_ay = None
        self._smooth_az = None
        self._display_range = 20.0
        self.history_x = [0.0] * 60
        self.history_y = [0.0] * 60
        self.history_z = [0.0] * 60
        self.setMinimumSize(140, 120)

    def set_accel(self, ax, ay, az):
        if ax is None or ay is None or az is None:
            return

        # Light smoothing keeps the traces readable when samples are noisy/spiky.
        alpha = 0.35
        self._smooth_ax = ax if self._smooth_ax is None else (self._smooth_ax * (1 - alpha) + ax * alpha)
        self._smooth_ay = ay if self._smooth_ay is None else (self._smooth_ay * (1 - alpha) + ay * alpha)
        self._smooth_az = az if self._smooth_az is None else (self._smooth_az * (1 - alpha) + az * alpha)

        sx = self._smooth_ax
        sy = self._smooth_ay
        sz = self._smooth_az

        self.ax = sx
        self.ay = sy
        self.az = sz

        if not self.has_data:
            # Avoid long flat-left section when the first packet arrives.
            self.history_x = [sx] * 60
            self.history_y = [sy] * 60
            self.history_z = [sz] * 60
            self.has_data = True
            self.update()
            return

        self.has_data = True
        self.history_x.append(sx); self.history_x.pop(0)
        self.history_y.append(sy); self.history_y.pop(0)
        self.history_z.append(sz); self.history_z.pop(0)
        self.update()

    def clear_data(self):
        self.ax = None
        self.ay = None
        self.az = None
        self.has_data = False
        self._smooth_ax = None
        self._smooth_ay = None
        self._smooth_az = None
        self._display_range = 20.0
        self.history_x = [0.0] * 60
        self.history_y = [0.0] * 60
        self.history_z = [0.0] * 60
        self.update()

    def _update_display_range(self):
        all_vals = self.history_x + self.history_y + self.history_z
        max_abs = max(abs(v) for v in all_vals) if all_vals else 0.0
        target = max(8.0, min(260.0, max_abs * 1.25))
        self._display_range = self._display_range * 0.85 + target * 0.15

    def _draw_trace(self, p, history, col, w, h, offset_y):
        path = QPainterPath()
        n = len(history)
        # Keep the trace inside the chart with adaptive range.
        px_per_unit = (h * 0.42) / max(self._display_range, 1e-3)
        for i, v in enumerate(history):
            x = i * w / n
            y = offset_y - v * px_per_unit
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(qc(col), 1))
        p.drawPath(path)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # background
        p.fillRect(0, 0, w, h, QBrush(qc(BG_CARD)))

        if not self.has_data:
            p.setPen(QPen(qc(TEXT_DIM)))
            p.setFont(QFont(SANS, 9, QFont.Weight.Medium))
            p.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "Waiting for data",
            )
            return

        # traces (clip upper area so text never overlaps)
        trace_h = max(28, h - 46)
        mid = trace_h // 2

        self._update_display_range()

        # grid + center line for readability
        p.setPen(QPen(QColor(80, 120, 170, 40), 1))
        for frac in (0.2, 0.4, 0.6, 0.8):
            y = int(trace_h * frac)
            p.drawLine(0, y, w, y)
        p.setPen(QPen(QColor(120, 170, 220, 85), 1))
        p.drawLine(0, mid, w, mid)

        # scale hints
        scale_font = QFont(MONO, 7)
        p.setFont(scale_font)
        p.setPen(QPen(qc(TEXT_DIM)))
        rng = self._display_range
        p.drawText(4, 10, f"+{rng:.0f}")
        p.drawText(4, trace_h - 2, f"-{rng:.0f}")

        p.save()
        p.setClipRect(0, 0, w, trace_h)
        self._draw_trace(p, self.history_x, DANGER,  w, trace_h, mid)
        self._draw_trace(p, self.history_y, ACCENT2, w, trace_h, mid)
        self._draw_trace(p, self.history_z, ACCENT,  w, trace_h, mid)
        p.restore()

        # current values
        label_font = QFont(SANS, 8)
        value_font = QFont(SANS, 10, QFont.Weight.DemiBold)
        labels = [("AX", f"{self.ax:.2f}", DANGER),
                  ("AY", f"{self.ay:.2f}", ACCENT2),
                  ("AZ", f"{self.az:.2f}", ACCENT)]
        label_y = h - 30
        value_y = h - 14
        for i, (lbl, val, col) in enumerate(labels):
            x = i * (w // 3)
            p.setFont(label_font)
            p.setPen(QPen(qc(TEXT_DIM)))
            p.drawText(x, label_y, w // 3, 12, Qt.AlignmentFlag.AlignCenter, lbl)
            p.setFont(value_font)
            p.setPen(QPen(qc(col)))
            p.drawText(x, value_y, w // 3, 12, Qt.AlignmentFlag.AlignCenter, val)


# ═══════════════════════════════════════════════════════════════════════════════
#  INSTRUMENT PANEL CARD
# ═══════════════════════════════════════════════════════════════════════════════

def make_card(title, widget, sub_labels=None):
    """Wrap an instrument in a dark panel card with a title."""
    card = QFrame()
    card.setObjectName("InstrumentCard")
    card.setStyleSheet(f"""
        #InstrumentCard {{
            background: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: 4px;
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 6, 8, 8)
    layout.setSpacing(4)

    title_lbl = QLabel(title.upper())
    title_lbl.setFont(QFont(MONO, 8))
    title_lbl.setStyleSheet(f"color: {TEXT_DIM}; letter-spacing: 2px;")
    layout.addWidget(title_lbl, alignment=Qt.AlignmentFlag.AlignLeft)

    layout.addWidget(widget, 1)

    if sub_labels:
        row = QHBoxLayout()
        for key, obj_name in sub_labels:
            col_w = QWidget()
            col_l = QVBoxLayout(col_w)
            col_l.setContentsMargins(0, 0, 0, 0)
            col_l.setSpacing(1)
            k_lbl = QLabel(key.upper())
            k_lbl.setFont(QFont(MONO, 7))
            k_lbl.setStyleSheet(f"color: {TEXT_DIM}; letter-spacing: 1px;")
            v_lbl = QLabel("0.0°")
            v_lbl.setObjectName(obj_name)
            v_lbl.setFont(QFont(MONO, 11, QFont.Weight.Bold))
            v_lbl.setStyleSheet(f"color: {WARN};")
            col_l.addWidget(k_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col_l.addWidget(v_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addWidget(col_w)
        layout.addLayout(row)

    return card


# ═══════════════════════════════════════════════════════════════════════════════
#  SERIAL READER THREAD
# ═══════════════════════════════════════════════════════════════════════════════

class SerialReaderThread(QThread):
    line_received = pyqtSignal(str)
    telemetry     = pyqtSignal(dict)

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port   = port
        self.baud   = baud
        self._stop  = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1)
        except Exception as e:
            self.line_received.emit(f"[SERIAL ERROR] {e}")
            return

        while not self._stop.is_set():
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                self.line_received.emit(line)
                self._parse(line)
            except Exception as e:
                self.line_received.emit(f"[READ ERROR] {e}")
                break
        ser.close()

    def _parse(self, line):
        """
        Expected CSV-ish format:
        PITCH:0.0,ROLL:0.0,HEADING:0,ALT:0.0,AX:0.00,AY:0.00,AZ:9.81,RSSI:-80
        """
        data = {}

        def parse_num(raw):
            value = raw.strip()
            if value.upper() in {"NONE", "NULL", "N/A", "NA", "-"}:
                return None
            # Handles values like "326.0cm" by reading the leading number.
            match = re.match(r"^[+-]?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group(0))
            return float(value)

        try:
            parts = line.split(",")
            for part in parts:
                if ":" in part:
                    k, v = part.split(":", 1)
                    data[k.strip().upper()] = parse_num(v)
        except Exception:
            pass

        # Fallback for lines like:
        # "Received Telemetry: d=326.0 cm gx=None gy=None gz=None"
        if not data:
            try:
                text = line
                if ":" in line:
                    text = line.split(":", 1)[1]

                for k, v in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,\s]+)", text):
                    data[k.strip().upper()] = parse_num(v)
            except Exception:
                pass

        if data:
            self.telemetry.emit(data)


# ═══════════════════════════════════════════════════════════════════════════════
#  ANCHOR PROCESS READER THREAD
# ═══════════════════════════════════════════════════════════════════════════════

class AnchorReader(QThread):
    line_received = pyqtSignal(str)
    finished_sig  = pyqtSignal()

    def __init__(self, proc):
        super().__init__()
        self.proc = proc

    def run(self):
        try:
            for line in iter(self.proc.stdout.readline, b""):
                decoded = line.decode("utf-8", errors="replace").rstrip()
                self.line_received.emit(decoded)
        except Exception:
            pass
        self.finished_sig.emit()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

GLOBAL_STYLE = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "{SANS}";
    font-size: 12px;
}}
QComboBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 10px;
    color: {TEXT};
    selection-background-color: {BORDER};
    min-width: 100px;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {BORDER};
    color: {TEXT};
}}
QPushButton {{
    background: {BG_PANEL};
    border: 1px solid {TEXT_DIM};
    border-radius: 3px;
    padding: 5px 12px;
    color: {TEXT};
    font-family: "{SANS}";
    font-size: 11px;
    letter-spacing: 0.5px;
    min-height: 30px;
}}
QPushButton:hover {{ background: {BORDER}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {GRID_LINE}; }}
QPushButton:checked {{
    color: {ACCENT2};
    border-color: {ACCENT2};
    background: rgba(0, 255, 157, 0.10);
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER};
}}
QScrollBar:vertical {{
    background: {BG_PANEL};
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QTextEdit {{
    background: {BG_PANEL};
    border: none;
    color: {ACCENT2};
    font-family: "Consolas";
    font-size: 11px;
}}
"""


class DashboardWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SENSOR TELEMETRY DASHBOARD")
        self.resize(1300, 820)
        self.setStyleSheet(GLOBAL_STYLE)

        self._serial_thread  = None
        self._anchor_proc    = None
        self._anchor_reader  = None
        self._anchor_running = False
        self._connected_port = None
        self._csv_file       = None
        self._csv_writer     = None
        self._csv_path       = None
        self._latest_telemetry = {
            "PITCH": 0.0,
            "ROLL": 0.0,
            "HEADING": 0.0,
            "ALT": 0.0,
            "AX": 0.0,
            "AY": 0.0,
            "AZ": 9.81,
            "RSSI": -80.0,
        }

        self._build_ui()
        self._refresh_ports()

        # demo animation timer (replaces real serial when disconnected)
        self._demo_t = 0.0
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._demo_tick)
        self._demo_timer.start(50)

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_node_bar())

        # main content
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 8, 12, 8)
        content_layout.setSpacing(8)

        content_layout.addWidget(self._build_instruments(), 3)
        content_layout.addWidget(self._build_serial_log(),  1)

        root_layout.addWidget(content, 1)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = QFrame()
        hdr.setFixedHeight(58)
        hdr.setStyleSheet(f"""
            background: {BG_PANEL};
            border-bottom: 1px solid {BORDER};
        """)
        layout = QHBoxLayout(hdr)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        # port label + combo
        port_lbl = QLabel("Serial Port")
        port_lbl.setFont(QFont(SANS, 10, QFont.Weight.Medium))
        port_lbl.setStyleSheet(f"color: {TEXT_DIM};")

        self.port_combo = QComboBox()
        self.port_combo.setFixedWidth(132)
        self.port_combo.setFixedHeight(32)
        self.port_combo.setFont(QFont(SANS, 10))

        # Clear, readable REFRESH button
        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setFixedWidth(96)
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setFont(QFont(SANS, 10, QFont.Weight.Medium))
        self.refresh_btn.setToolTip("Scan for available serial ports")
        self.refresh_btn.clicked.connect(self._refresh_ports)

        # separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {BORDER};")

        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.setFixedWidth(120)
        self.connect_btn.setFixedHeight(32)
        self.connect_btn.setFont(QFont(SANS, 10, QFont.Weight.Bold))
        self.connect_btn.clicked.connect(self._on_connect)

        self.disconnect_btn = QPushButton("DISCONNECT")
        self.disconnect_btn.setFixedWidth(128)
        self.disconnect_btn.setFixedHeight(32)
        self.disconnect_btn.setFont(QFont(SANS, 10))
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect)

        # separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {BORDER};")

        self.log_csv_btn = QPushButton("LOG CSV")
        self.log_csv_btn.setCheckable(True)
        self.log_csv_btn.setFixedWidth(96)
        self.log_csv_btn.setFixedHeight(32)
        self.log_csv_btn.setFont(QFont(SANS, 10))
        self.log_csv_btn.toggled.connect(self._on_log_csv_toggled)

        layout.addWidget(port_lbl)
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(sep)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.disconnect_btn)
        layout.addWidget(sep2)
        layout.addWidget(self.log_csv_btn)
        layout.addStretch()

        # status badge – right-aligned
        self.status_badge = QLabel("● NOT CONNECTED")
        self.status_badge.setFont(QFont(SANS, 10, QFont.Weight.Medium))
        self.status_badge.setStyleSheet(
            f"color: {DANGER}; border: 1px solid {DANGER};"
            f"border-radius: 3px; padding: 2px 10px;"
        )
        layout.addWidget(self.status_badge)

        return hdr

    # ── Node Bar ──────────────────────────────────────────────────────────────

    def _build_node_bar(self):
        bar = QFrame()
        bar.setFixedHeight(34)
        bar.setStyleSheet(f"background: {BG}; border-bottom: 1px solid {BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)

        lbl = QLabel("LIVE SENSOR NODES · CLICK A CARD TO VIEW ITS INSTRUMENTS")
        lbl.setFont(QFont(MONO, 9))
        lbl.setStyleSheet(f"color: {TEXT_DIM}; letter-spacing: 2px;")
        layout.addWidget(lbl)
        layout.addStretch()

        self.node_placeholder = QLabel(
            "Waiting for sensor data…  connect a serial port to begin.")
        self.node_placeholder.setFont(QFont(MONO, 9))
        self.node_placeholder.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(self.node_placeholder)

        return bar

    # ── Instruments ───────────────────────────────────────────────────────────

    def _build_instruments(self):
        panel = QFrame()
        panel.setStyleSheet(f"""
            background: {BG_PANEL};
            border: 1px solid {BORDER};
            border-radius: 4px;
        """)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        # section label
        sec_lbl = QLabel("AHRS · COMPASS · RADAR · ACCELEROMETER")
        sec_lbl.setFont(QFont(MONO, 8))
        sec_lbl.setStyleSheet(f"color: {TEXT_DIM}; letter-spacing: 2px;")
        layout.addWidget(sec_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)

        # Artificial Horizon
        self.horizon = ArtificialHorizon()
        ah_card = make_card(
            "Artificial Horizon",
            self.horizon,
            [("Pitch", "pitch_lbl"), ("Roll", "roll_lbl")]
        )
        self.pitch_lbl = ah_card.findChild(QLabel, "pitch_lbl")
        self.roll_lbl  = ah_card.findChild(QLabel, "roll_lbl")
        row.addWidget(ah_card, 2)

        # Compass
        self.compass = CompassHSI()
        cmp_card = make_card(
            "Compass · HSI",
            self.compass,
            [("Heading / Yaw", "hdg_lbl")]
        )
        self.hdg_lbl = cmp_card.findChild(QLabel, "hdg_lbl")
        if self.hdg_lbl:
            self.hdg_lbl.setText("000°")
            self.hdg_lbl.setStyleSheet(f"color: {ACCENT};")
        row.addWidget(cmp_card, 2)

        # Radar
        self.radar = RadarNodeMap()
        rdr_card = make_card(
            "Radar · Node Map",
            self.radar,
            [("Nodes Detected", "nodes_lbl")]
        )
        self.nodes_lbl = rdr_card.findChild(QLabel, "nodes_lbl")
        if self.nodes_lbl:
            self.nodes_lbl.setText("0")
            self.nodes_lbl.setStyleSheet(f"color: {ACCENT};")
        row.addWidget(rdr_card, 2)

        # Right column: altitude + accel
        right = QVBoxLayout()
        right.setSpacing(8)

        # Altitude/Signal removed from UI
        self.alt_bar = None
        self.alt_val_lbl = None
        self.sig_bar = None
        self.sig_val_lbl = None

        # Accelerometer
        self.accel = AccelReadout()
        acc_card = make_card("Accelerometer", self.accel)
        right.addWidget(acc_card, 1)
        right.addStretch(1)

        right_w = QWidget()
        right_w.setLayout(right)
        row.addWidget(right_w, 2)

        layout.addLayout(row, 1)
        return panel

    # ── Serial Log ────────────────────────────────────────────────────────────

    def _build_serial_log(self):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                border: 1px dashed {TEXT_DIM};
                border-radius: 4px;
                background: {BG_PANEL};
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(4)

        hdr = QHBoxLayout()
        lbl = QLabel("SERIAL LOG")
        lbl.setFont(QFont(MONO, 9))
        lbl.setStyleSheet(f"color: {TEXT_DIM}; letter-spacing: 2px; border: none;")
        hdr.addWidget(lbl)
        hdr.addStretch()

        clr_btn = QPushButton("CLEAR")
        clr_btn.setFixedWidth(70)
        clr_btn.setFixedHeight(28)
        clr_btn.setFont(QFont(SANS, 9))
        clr_btn.clicked.connect(self._clear_log)
        hdr.addWidget(clr_btn)
        layout.addLayout(hdr)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(100)
        self.log_box.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: {ACCENT2};
                font-family: "{MONO}";
                font-size: 11px;
                selection-background-color: {BORDER};
            }}
        """)
        # prevent scroll into empty space
        self.log_box.verticalScrollBar().rangeChanged.connect(
            self._clamp_scroll
        )
        layout.addWidget(self.log_box, 1)

        self._log("Waiting for data…", color=TEXT_DIM)
        return frame

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, text, color=None):
        if color is None:
            color = ACCENT2

        # Special formatting for known Anchor strings
        if "Anchor Mirroring Mode" in text:
            color = WARN
        elif "Received Mimic:" in text:
            color = ACCENT

        cursor = self.log_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        html = (
            f'<span style="color:{color}; font-family:{MONO};">'
            f'{text.replace("<","&lt;").replace(">","&gt;")}</span><br>'
        )
        cursor.insertHtml(html)
        # auto-scroll
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clamp_scroll(self, _min, _max):
        sb = self.log_box.verticalScrollBar()
        if sb.value() > _max:
            sb.setValue(_max)

    def _clear_log(self):
        self.log_box.clear()

    def _on_log_csv_toggled(self, checked):
        if checked:
            self._start_csv_logging()
        else:
            self._stop_csv_logging(log_message=True)

    def _start_csv_logging(self):
        if self._csv_file:
            return

        base_dir = os.path.dirname(os.path.abspath(__file__))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv_path = os.path.join(base_dir, f"sensor_log_{ts}.csv")

        try:
            self._csv_file = open(self._csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow([
                "timestamp",
                "pitch",
                "roll",
                "heading",
                "alt",
                "ax",
                "ay",
                "az",
                "rssi",
            ])
            self._csv_file.flush()
            self._log("Logging data...", color=ACCENT2)
            self._log(f"[CSV] {os.path.basename(self._csv_path)}", color=TEXT_DIM)
        except Exception as e:
            self._log(f"[CSV ERROR] {e}", color=DANGER)
            self._csv_file = None
            self._csv_writer = None
            self._csv_path = None
            self.log_csv_btn.blockSignals(True)
            self.log_csv_btn.setChecked(False)
            self.log_csv_btn.blockSignals(False)

    def _stop_csv_logging(self, log_message=False):
        if self._csv_file:
            try:
                self._csv_file.flush()
                self._csv_file.close()
            except Exception:
                pass
        self._csv_file = None
        self._csv_writer = None
        self._csv_path = None

        if log_message:
            self._log("[CSV] Logging stopped.", color=WARN)

    def _write_csv_row(self):
        if not self._csv_writer:
            return
        try:
            now = datetime.now().isoformat(timespec="milliseconds")
            self._csv_writer.writerow([
                now,
                self._latest_telemetry["PITCH"],
                self._latest_telemetry["ROLL"],
                self._latest_telemetry["HEADING"],
                self._latest_telemetry["ALT"],
                self._latest_telemetry["AX"],
                self._latest_telemetry["AY"],
                self._latest_telemetry["AZ"],
                self._latest_telemetry["RSSI"],
            ])
            self._csv_file.flush()
        except Exception as e:
            self._log(f"[CSV ERROR] {e}", color=DANGER)
            self.log_csv_btn.blockSignals(True)
            self.log_csv_btn.setChecked(False)
            self.log_csv_btn.blockSignals(False)
            self._stop_csv_logging(log_message=False)

    # ── Port Management ───────────────────────────────────────────────────────

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if ports:
            self.port_combo.addItems(ports)
        else:
            self.port_combo.addItem("(none)")

    def _pick_anchor_port(self):
        """Pick a likely Anchor Pico port (not the telemetry/Scout COM port)."""
        ports = [p.device for p in serial.tools.list_ports.comports()]
        in_use = self._connected_port

        if in_use:
            candidates = [p for p in ports if p != in_use]
            if candidates:
                return candidates[0]
            # If only telemetry port is visible, let mpremote auto-detect
            # This handles the case where Anchor Pico is on same physical device
            return "auto"

        # No port connected, return auto-detect
        return "auto"

    def _on_connect(self):
        port = self.port_combo.currentText()
        if port in ("", "(none)"):
            self._log("[ERROR] No port selected.", color=DANGER)
            return
        if self._serial_thread and self._serial_thread.isRunning():
            return

        self._serial_thread = SerialReaderThread(port, 115200)
        self._serial_thread.line_received.connect(self._on_serial_line)
        self._serial_thread.telemetry.connect(self._on_telemetry)
        self._serial_thread.start()
        self._connected_port = port
        self.horizon.clear_data()
        self.accel.clear_data()

        self.status_badge.setText(f"● Connected — {port} @ 115200 baud")
        self.status_badge.setStyleSheet(
            f"color: {ACCENT2}; border: 1px solid {ACCENT2};"
            f"border-radius: 3px; padding: 2px 8px;"
        )
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self._log(f"[SERIAL] Connected to {port} @ 115200", color=ACCENT2)

        # Auto-launch Anchor.py as soon as serial connects
        if not self._anchor_running:
            self._start_anchor()

    def _on_disconnect(self):
        if self._serial_thread:
            self._serial_thread.stop()
            self._serial_thread.wait(2000)
            self._serial_thread = None
        self._connected_port = None

        # Stop Anchor process when serial disconnects
        if self._anchor_running:
            self._stop_anchor()

        self.status_badge.setText("● NOT CONNECTED")
        self.status_badge.setStyleSheet(
            f"color: {DANGER}; border: 1px solid {DANGER};"
            f"border-radius: 3px; padding: 2px 8px;"
        )
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.horizon.clear_data()
        self.accel.clear_data()
        self._log("[SERIAL] Disconnected.", color=WARN)

    # ── Anchor ────────────────────────────────────────────────────────────────

    def _toggle_anchor(self):
        if self._anchor_running:
            self._stop_anchor()
        else:
            self._start_anchor()

    def _start_anchor(self):
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        )
        anchor_candidates = [
            os.path.join(project_root, "Anchor", "Anchor.py"),
            os.path.join(project_root, "Anchor.py"),
        ]
        anchor_path = next((p for p in anchor_candidates if os.path.exists(p)), None)
        if not anchor_path:
            self._log(
                "[ERROR] Anchor.py not found. Expected Anchor/Anchor.py or Anchor.py",
                color=DANGER,
            )
            return

        mpremote = shutil.which("mpremote")
        if not mpremote:
            self._log(
                "[ERROR] mpremote not found. Install it so Anchor.py runs on Pico.",
                color=DANGER,
            )
            return

        anchor_port = self._pick_anchor_port()
        if not anchor_port:
            self._log(
                "[ERROR] Anchor Pico port not found. Connect Anchor on a different COM port than telemetry.",
                color=DANGER,
            )
            return

        # Temporarily disconnect telemetry serial to allow mpremote to access the device
        was_serial_running = False
        if self._serial_thread and self._serial_thread.isRunning():
            was_serial_running = True
            self._log("[ANCHOR] Pausing telemetry to allow mpremote device access...", color=WARN)
            self._serial_thread.stop()
            self._serial_thread.wait(1000)
            time.sleep(0.5)  # Give OS time to release the port

        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                project_root if not existing_pp else f"{project_root}{os.pathsep}{existing_pp}"
            )

            # Build command: if anchor_port is "auto", skip the connect part
            if anchor_port == "auto":
                cmd = [mpremote, "run", anchor_path]
            else:
                cmd = [mpremote, "connect", anchor_port, "run", anchor_path]

            self._anchor_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                cwd=project_root,
                env=env,
            )
            
            # Keep telemetry paused while Anchor is running (mpremote holds the port)
            # Telemetry will restart when Anchor stops
        except Exception as e:
            self._log(f"[ERROR] Could not launch Anchor.py: {e}", color=DANGER)
            # Restart telemetry on error if it was running
            if was_serial_running:
                time.sleep(0.5)
                self._serial_thread = SerialReaderThread(self._connected_port, 115200)
                self._serial_thread.line_received.connect(self._on_serial_line)
                self._serial_thread.telemetry.connect(self._on_telemetry)
                self._serial_thread.start()
                self._log("[ANCHOR] Telemetry resumed after error.", color=ACCENT2)
            return

        self._anchor_reader = AnchorReader(self._anchor_proc)
        self._anchor_reader.line_received.connect(self._on_anchor_line)
        self._anchor_reader.finished_sig.connect(self._on_anchor_finished)
        self._anchor_reader.start()

        self._anchor_running = True
        self._update_anchor_btn(running=True)
        self._log(f"[ANCHOR] Anchor.py started on Pico via mpremote ({anchor_port}).", color=ACCENT2)

    def _stop_anchor(self):
        if self._anchor_proc:
            try:
                self._anchor_proc.terminate()
                self._anchor_proc.wait(timeout=3)
            except Exception:
                try:
                    self._anchor_proc.kill()
                except Exception:
                    pass
            self._anchor_proc = None

        self._anchor_running = False
        self._update_anchor_btn(running=False)
        self._log("[ANCHOR] Anchor.py stopped.", color=WARN)
        
        # Restart telemetry after Anchor stops
        time.sleep(0.5)  # Give OS time to release the port
        if self._connected_port:
            self._serial_thread = SerialReaderThread(self._connected_port, 115200)
            self._serial_thread.line_received.connect(self._on_serial_line)
            self._serial_thread.telemetry.connect(self._on_telemetry)
            self._serial_thread.start()
            self._log("[ANCHOR] Telemetry resumed.", color=ACCENT2)

    def _on_anchor_finished(self):
        self._anchor_running = False
        self._anchor_proc    = None
        self._update_anchor_btn(running=False)
        self._log("[ANCHOR] Anchor.py process exited.", color=WARN)
        
        # Restart telemetry after Anchor stops
        time.sleep(0.5)  # Give OS time to release the port
        if self._connected_port:
            self._serial_thread = SerialReaderThread(self._connected_port, 115200)
            self._serial_thread.line_received.connect(self._on_serial_line)
            self._serial_thread.telemetry.connect(self._on_telemetry)
            self._serial_thread.start()
            self._log("[ANCHOR] Telemetry resumed.", color=ACCENT2)

    def _update_anchor_btn(self, running):
        if not hasattr(self, "anchor_btn"):
            return

        if running:
            self.anchor_btn.setText("■ STOP")
            self.anchor_btn.setStyleSheet(f"""
                color: {DANGER};
                border: 1px solid {DANGER};
                border-radius: 3px;
                padding: 4px 14px;
                background: rgba(255,56,96,0.12);
            """)
            # glow effect
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(18)
            glow.setColor(QColor(DANGER))
            glow.setOffset(0, 0)
            self.anchor_btn.setGraphicsEffect(glow)
        else:
            self.anchor_btn.setText("▶ RUN ANCHOR")
            self.anchor_btn.setStyleSheet(
                f"color: {ACCENT}; border-color: {ACCENT};"
            )
            self.anchor_btn.setGraphicsEffect(None)

    # ── Telemetry Updates ─────────────────────────────────────────────────────

    def _on_serial_line(self, line):
        self._log(line)

    def _parse_num(self, raw):
        value = str(raw).strip()
        if value.upper() in {"NONE", "NULL", "N/A", "NA", "-"}:
            return None
        match = re.match(r"^[+-]?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
        try:
            return float(value)
        except Exception:
            return None

    def _extract_telemetry_from_line(self, line):
        data = {}
        text = line.split(":", 1)[1] if ":" in line else line
        for k, v in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,\s]+)", text):
            parsed = self._parse_num(v)
            if parsed is not None:
                data[k.strip().upper()] = parsed
            elif k.strip().upper() in {"GX", "GY", "GZ", "AX", "AY", "AZ"}:
                # Keep explicit None for accel keys so waiting logic can still apply.
                data[k.strip().upper()] = None
        return data

    def _on_anchor_line(self, line):
        self._log(f"[ANCHOR] {line}", color=ACCENT)
        data = self._extract_telemetry_from_line(line)
        if data:
            self._on_telemetry(data)

    def _on_telemetry(self, data):
        self._latest_telemetry.update(data)

        pitch = data.get("PITCH")
        if pitch is None and "PICH" in data:
            # Accept typo alias from upstream sender if present.
            pitch = data.get("PICH")
        roll = data.get("ROLL")

        if pitch is not None and roll is not None:
            self._latest_telemetry["PITCH"] = pitch
            self._latest_telemetry["ROLL"] = roll
            self.horizon.set_attitude(pitch, roll)
            self._update_attitude_labels(pitch, roll)

        if "HEADING" in data:
            h = data["HEADING"]
            self.compass.set_heading(h)
            if self.hdg_lbl:
                self.hdg_lbl.setText(f"{int(h):03d}°")

        if "ALT" in data:
            v = data["ALT"]
            if self.alt_bar:
                self.alt_bar.set_value(v)
            if self.alt_val_lbl:
                self.alt_val_lbl.setText(f"{v:.1f} m")

        if "RSSI" in data:
            v = data["RSSI"]
            if self.sig_bar:
                self.sig_bar.set_value(v)
            if self.sig_val_lbl:
                self.sig_val_lbl.setText(f"{v:.0f} dBm")

        # Accelerometer graph is driven strictly by AX/AY/AZ values.
        if "AX" in data or "AY" in data or "AZ" in data:
            ax = data.get("AX")
            ay = data.get("AY")
            az = data.get("AZ")
            if ax is not None and ay is not None and az is not None:
                self._latest_telemetry["AX"] = ax
                self._latest_telemetry["AY"] = ay
                self._latest_telemetry["AZ"] = az
                self.accel.set_accel(ax, ay, az)

        if self._csv_writer:
            self._write_csv_row()

    def _update_attitude_labels(self, pitch, roll):
        if self.pitch_lbl:
            self.pitch_lbl.setText(f"{pitch:.1f}°")
        if self.roll_lbl:
            self.roll_lbl.setText(f"{roll:.1f}°")

    # ── Demo Animation (when no serial connected) ─────────────────────────────

    def _demo_tick(self):
        if self._connected_port or self._anchor_running:
            return   # real data takes over while connected/anchor active

        if self._serial_thread and self._serial_thread.isRunning():
            return   # real data takes over

        t = self._demo_t
        self._demo_t += 0.04

        pitch   = 8  * math.sin(t * 0.4)
        roll    = 12 * math.sin(t * 0.25 + 1)
        heading = (t * 6) % 360
        alt     = 40 + 30 * math.sin(t * 0.15)
        rssi    = -65 + 10 * math.sin(t * 0.3)
        ax      = 0.5 * math.sin(t * 1.1)
        ay      = 0.3 * math.cos(t * 0.9)
        az      = 9.81 + 0.2 * math.sin(t * 2)

        # Keep horizon in data-driven mode (no synthetic values).
        self.compass.set_heading(heading)
        if self.hdg_lbl:
            self.hdg_lbl.setText(f"{int(heading):03d}°")
        if self.alt_bar:
            self.alt_bar.set_value(alt)
        if self.alt_val_lbl:
            self.alt_val_lbl.setText(f"{alt:.1f} m")
        if self.sig_bar:
            self.sig_bar.set_value(rssi)
        if self.sig_val_lbl:
            self.sig_val_lbl.setText(f"{rssi:.0f} dBm")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._stop_anchor()
        self._stop_csv_logging(log_message=False)
        if self._serial_thread:
            self._serial_thread.stop()
            self._serial_thread.wait(2000)
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette baseline
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base,            QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.Text,            QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button,          QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(BORDER))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT))
    app.setPalette(pal)

    win = DashboardWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()