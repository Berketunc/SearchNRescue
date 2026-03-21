"""
IoT Sensor Network Dashboard
Real-time monitoring for nRF24L01 nodes via Raspberry Pi Pico 2
"""

import asyncio
import json
import csv
import os
from datetime import datetime
from collections import defaultdict, deque
import serial
import serial.tools.list_ports
from nicegui import ui, app

# ── Configuration ────────────────────────────────────────────────────────────
BAUD_RATE       = 115200
MAX_HISTORY     = 60          # data points kept per node for the graph
CSV_FILENAME    = "sensor_log.csv"
POLL_INTERVAL   = 0.05        # seconds between serial reads

# ── State ─────────────────────────────────────────────────────────────────────
sensor_data: dict[int, dict]                   = {}   # latest reading per node
history:     dict[int, deque]                  = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
timestamps:  deque                             = deque(maxlen=MAX_HISTORY)

serial_port:  serial.Serial | None            = None
logging_active: bool                          = False
csv_writer_obj: csv.DictWriter | None         = None
csv_file_handle                               = None
status_message: str                           = "Disconnected"
port_name: str                                = ""
raw_log_lines: deque                          = deque(maxlen=200)

# ── UI element holders (populated after ui.run) ───────────────────────────────
cards_container   = None
graph_plot        = None
status_label      = None
log_textarea      = None
port_select       = None
csv_toggle_btn    = None

# ── Serial helpers ─────────────────────────────────────────────────────────────
def list_serial_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


def open_serial(port: str) -> str | None:
    """Attempt to open serial port. Returns error string or None on success."""
    global serial_port, port_name, status_message
    if serial_port and serial_port.is_open:
        serial_port.close()
    try:
        serial_port = serial.Serial(port, BAUD_RATE, timeout=0)
        port_name = port
        status_message = f"Connected — {port} @ {BAUD_RATE} baud"
        return None
    except serial.SerialException as e:
        err = str(e)
        if "PermissionError" in err or "Access is denied" in err or "busy" in err.lower():
            status_message = f"Port busy / permission denied: {port}"
            return f"Port {port} is busy or access denied. Close other terminals / IDEs using it."
        status_message = f"Failed to open {port}: {err}"
        return f"Could not open {port}: {err}"


def close_serial():
    global serial_port, status_message
    if serial_port and serial_port.is_open:
        serial_port.close()
    serial_port = None
    status_message = "Disconnected"


def parse_line(raw: str) -> dict | None:
    """Parse a JSON line; return dict or None on failure."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if "id" not in data:
            return None
        return data
    except json.JSONDecodeError:
        return None


# ── CSV logging ───────────────────────────────────────────────────────────────
def start_csv_logging():
    global csv_writer_obj, csv_file_handle, logging_active
    file_exists = os.path.isfile(CSV_FILENAME)
    csv_file_handle = open(CSV_FILENAME, "a", newline="")
    fieldnames = ["timestamp", "id", "temp", "humidity", "rssi"]
    csv_writer_obj = csv.DictWriter(csv_file_handle, fieldnames=fieldnames, extrasaction="ignore")
    if not file_exists:
        csv_writer_obj.writeheader()
    logging_active = True


def stop_csv_logging():
    global csv_writer_obj, csv_file_handle, logging_active
    if csv_file_handle:
        csv_file_handle.close()
    csv_writer_obj = None
    csv_file_handle = None
    logging_active = False


def log_row(data: dict):
    if logging_active and csv_writer_obj:
        row = {**data, "timestamp": datetime.now().isoformat(timespec="seconds")}
        csv_writer_obj.writerow(row)
        csv_file_handle.flush()


# ── Background serial reader ──────────────────────────────────────────────────
async def serial_reader():
    """Continuously reads serial port and updates shared state."""
    buffer = ""
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if not serial_port or not serial_port.is_open:
            continue
        try:
            waiting = serial_port.in_waiting
            if waiting:
                chunk = serial_port.read(waiting).decode("utf-8", errors="replace")
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    ts = datetime.now().strftime("%H:%M:%S")
                    raw_log_lines.append(f"[{ts}] {line.strip()}")
                    parsed = parse_line(line)
                    if parsed:
                        node_id = int(parsed["id"])
                        sensor_data[node_id] = parsed
                        history[node_id].append(parsed.get("temp", 0))
                        if len(timestamps) == 0 or timestamps[-1] != ts:
                            timestamps.append(ts)
                        if logging_active:
                            log_row(parsed)
        except serial.SerialException:
            close_serial()


# ── UI refresh loop ───────────────────────────────────────────────────────────
def make_rssi_bar(rssi: int) -> str:
    """Convert RSSI to a coloured strength label."""
    if rssi >= -60:
        return "Excellent", "#00d4aa"
    elif rssi >= -70:
        return "Good", "#7ecb20"
    elif rssi >= -80:
        return "Fair", "#f5a623"
    else:
        return "Weak", "#ff4757"


def build_card(container, node_id: int, data: dict):
    rssi_label, rssi_color = make_rssi_bar(data.get("rssi", -100))
    temp  = data.get("temp", "–")
    hum   = data.get("humidity", "–")
    rssi  = data.get("rssi", "–")

    with container:
        with ui.card().classes("sensor-card"):
            with ui.row().classes("card-header"):
                ui.label(f"NODE {node_id:02d}").classes("node-id")
                ui.badge(rssi_label).style(
                    f"background:{rssi_color};color:#0a0f1e;font-weight:700;font-size:0.7rem"
                )
            with ui.row().classes("metrics-row"):
                with ui.column().classes("metric"):
                    ui.label("TEMP").classes("metric-label")
                    ui.label(f"{temp}°C").classes("metric-value temp-value")
                with ui.column().classes("metric"):
                    ui.label("HUMIDITY").classes("metric-label")
                    ui.label(f"{hum}%").classes("metric-value hum-value")
                with ui.column().classes("metric"):
                    ui.label("RSSI").classes("metric-label")
                    ui.label(f"{rssi} dBm").classes("metric-value rssi-value").style(
                        f"color:{rssi_color}"
                    )


async def ui_updater():
    """Refreshes cards, graph, status, and log every 500 ms."""
    known_nodes: set[int] = set()

    while True:
        await asyncio.sleep(0.5)

        # Status label
        if status_label:
            is_conn = serial_port and serial_port.is_open
            status_label.set_text(status_message)
            status_label.style(
                f"color:{'#00d4aa' if is_conn else '#ff4757'}"
            )

        # Sensor cards – rebuild if new nodes appeared
        if cards_container and set(sensor_data.keys()) != known_nodes:
            cards_container.clear()
            known_nodes = set(sensor_data.keys())
            for nid in sorted(known_nodes):
                build_card(cards_container, nid, sensor_data[nid])
        elif cards_container and known_nodes:
            # Just refresh values without rebuilding DOM
            cards_container.clear()
            for nid in sorted(known_nodes):
                build_card(cards_container, nid, sensor_data[nid])

        # Plotly graph
        if graph_plot and sensor_data:
            traces = []
            ts_list = list(timestamps)
            for nid in sorted(sensor_data.keys()):
                h = list(history[nid])
                # align to same length as ts_list (pad left with None)
                if len(h) < len(ts_list):
                    h = [None] * (len(ts_list) - len(h)) + h
                traces.append({
                    "x": ts_list[-len(h):],
                    "y": h,
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": f"Node {nid}",
                    "line": {"width": 2},
                    "marker": {"size": 4},
                })
            graph_plot.update_figure({
                "data": traces,
                "layout": {
                    "paper_bgcolor": "#0d1526",
                    "plot_bgcolor":  "#0d1526",
                    "font":   {"color": "#c8d6f0", "family": "JetBrains Mono, monospace", "size": 11},
                    "xaxis":  {"gridcolor": "#1e2d4a", "showgrid": True, "title": "Time"},
                    "yaxis":  {"gridcolor": "#1e2d4a", "showgrid": True, "title": "Temperature (°C)"},
                    "legend": {"bgcolor": "#0a0f1e", "bordercolor": "#1e2d4a", "borderwidth": 1},
                    "margin": {"l": 50, "r": 20, "t": 20, "b": 50},
                    "hovermode": "x unified",
                }
            })

        # Raw log textarea
        if log_textarea:
            log_textarea.set_value("\n".join(raw_log_lines))


# ── Page construction ─────────────────────────────────────────────────────────
@ui.page("/")
def index():
    global cards_container, graph_plot, status_label, log_textarea, port_select, csv_toggle_btn

    # ── Global CSS ──────────────────────────────────────────────────────────
    ui.add_head_html("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Space+Grotesk:wght@300;500;700&display=swap" rel="stylesheet">
    <style>
      :root {
        --bg-deep:    #070c18;
        --bg-card:    #0d1526;
        --bg-panel:   #111c30;
        --accent-teal:#00d4aa;
        --accent-blue:#3d8bff;
        --text-pri:   #e8f0ff;
        --text-sec:   #7a8fad;
        --border:     #1a2a42;
      }

      body, .nicegui-content { background: var(--bg-deep) !important; font-family: 'JetBrains Mono', monospace; }

      /* ── Top bar ── */
      .topbar {
        background: linear-gradient(90deg, #070c18 0%, #0d1a32 60%, #070c18 100%);
        border-bottom: 1px solid var(--border);
        padding: 12px 24px;
        display: flex; align-items: center; gap: 16px;
      }
      .topbar-title {
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 700; font-size: 1.25rem;
        color: var(--text-pri); letter-spacing: 0.04em;
      }
      .topbar-title span { color: var(--accent-teal); }
      .live-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--accent-teal);
        box-shadow: 0 0 6px var(--accent-teal);
        animation: pulse 1.5s ease-in-out infinite;
      }
      @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

      /* ── Control strip ── */
      .control-strip {
        background: var(--bg-panel);
        border-bottom: 1px solid var(--border);
        padding: 10px 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
      }
      .control-strip select, .control-strip .q-select {
        background: var(--bg-card) !important; color: var(--text-pri) !important;
        border: 1px solid var(--border) !important; border-radius: 6px !important;
        font-family: 'JetBrains Mono', monospace; font-size: 0.82rem;
      }
      .status-chip {
        font-family: 'JetBrains Mono', monospace; font-size: 0.78rem;
        padding: 4px 12px; border-radius: 20px;
        background: var(--bg-deep); border: 1px solid var(--border);
        color: var(--text-sec);
      }

      /* ── Sensor cards ── */
      .sensor-card {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        padding: 16px !important; min-width: 200px;
        transition: border-color 0.3s, box-shadow 0.3s;
      }
      .sensor-card:hover {
        border-color: var(--accent-teal) !important;
        box-shadow: 0 0 18px rgba(0,212,170,0.12) !important;
      }
      .card-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }
      .node-id {
        font-family: 'Space Grotesk', sans-serif; font-weight: 700;
        font-size: 0.9rem; color: var(--accent-blue); letter-spacing: 0.1em;
      }
      .metrics-row { display:flex; gap:18px; }
      .metric { display:flex; flex-direction:column; gap:4px; }
      .metric-label { font-size:0.62rem; color: var(--text-sec); letter-spacing:0.12em; }
      .metric-value { font-size:1.35rem; font-weight:700; color: var(--text-pri); }
      .temp-value { color: #ff9f43; }
      .hum-value  { color: var(--accent-blue); }

      /* ── Section headers ── */
      .section-head {
        font-family: 'Space Grotesk', sans-serif; font-weight: 500; font-size: 0.78rem;
        color: var(--text-sec); letter-spacing: 0.14em; text-transform: uppercase;
        padding: 20px 24px 8px; border-bottom: 1px solid var(--border); margin-bottom: 16px;
      }

      /* ── Log area ── */
      textarea {
        background: var(--bg-deep) !important; color: #4af0b8 !important;
        font-family: 'JetBrains Mono', monospace !important; font-size: 0.75rem !important;
        border: 1px solid var(--border) !important; border-radius: 6px !important;
        resize: none;
      }

      /* ── Buttons ── */
      .q-btn { font-family: 'JetBrains Mono', monospace !important; font-size: 0.78rem !important; letter-spacing: 0.06em !important; }

      /* ── Empty state ── */
      .empty-state {
        color: var(--text-sec); font-size: 0.85rem; text-align: center;
        padding: 40px; border: 1px dashed var(--border); border-radius: 10px;
        width: 100%; letter-spacing: 0.05em;
      }

      /* ── Scrollbar ── */
      ::-webkit-scrollbar { width: 5px; height: 5px; }
      ::-webkit-scrollbar-track { background: var(--bg-deep); }
      ::-webkit-scrollbar-thumb { background: #1e2d4a; border-radius: 3px; }
    </style>
    """)

    # ── Top bar ────────────────────────────────────────────────────────────
    with ui.row().classes("topbar").style("width:100%"):
        ui.html('<div class="live-dot"></div>')
        ui.html('<div class="topbar-title">IoT Sensor <span>MESH</span> Dashboard</div>')
        ui.label("nRF24L01 · Pico 2 Anchor").style(
            "color:#3d5a7a;font-size:0.75rem;margin-left:auto"
        )

    # ── Control strip ──────────────────────────────────────────────────────
    with ui.row().classes("control-strip").style("width:100%"):
        port_select = ui.select(
            options=list_serial_ports() or ["(no ports found)"],
            label="Serial Port",
            value=(list_serial_ports() or [""])[0],
        ).style("min-width:180px;background:#0d1526;color:#e8f0ff")

        async def on_refresh_ports():
            ports = list_serial_ports()
            port_select.options = ports or ["(no ports found)"]
            port_select.update()

        ui.button("↻", on_click=on_refresh_ports).props("flat dense").style(
            "color:#3d8bff;font-size:1.1rem"
        )

        async def on_connect():
            sel = port_select.value
            if not sel or sel == "(no ports found)":
                ui.notify("Select a valid port first", type="warning")
                return
            err = open_serial(sel)
            if err:
                ui.notify(err, type="negative", multi_line=True, timeout=6000)
            else:
                ui.notify(f"Connected to {sel}", type="positive")

        async def on_disconnect():
            close_serial()
            ui.notify("Disconnected", type="info")

        ui.button("CONNECT", on_click=on_connect).props("unelevated").style(
            "background:#00d4aa;color:#070c18;font-weight:700"
        )
        ui.button("DISCONNECT", on_click=on_disconnect).props("unelevated").style(
            "background:#1a2a42;color:#7a8fad"
        )

        # CSV toggle
        async def toggle_csv():
            global logging_active
            if logging_active:
                stop_csv_logging()
                csv_toggle_btn.set_text("▶ LOG CSV")
                csv_toggle_btn.style("background:#1a2a42;color:#7a8fad")
                ui.notify(f"Logging stopped. Data saved to {CSV_FILENAME}", type="info")
            else:
                start_csv_logging()
                csv_toggle_btn.set_text("■ STOP LOG")
                csv_toggle_btn.style("background:#ff4757;color:#fff")
                ui.notify(f"Logging to {CSV_FILENAME}", type="positive")

        csv_toggle_btn = ui.button("▶ LOG CSV", on_click=toggle_csv).props("unelevated").style(
            "background:#1a2a42;color:#7a8fad;margin-left:auto"
        )

        status_label = ui.label(status_message).classes("status-chip").style("color:#ff4757")

    # ── Main content ───────────────────────────────────────────────────────
    with ui.column().style("width:100%;padding:0 24px;box-sizing:border-box;gap:0"):

        # Sensor cards section
        ui.html('<div class="section-head">Live Sensor Nodes</div>')
        cards_container = ui.row().style(
            "flex-wrap:wrap;gap:14px;padding:0 0 20px 0;min-height:120px"
        )
        with cards_container:
            ui.html('<div class="empty-state">Waiting for sensor data… connect a serial port to begin.</div>')

        # Temperature graph
        ui.html('<div class="section-head">Temperature Trend</div>')
        graph_plot = ui.plotly({
            "data": [],
            "layout": {
                "paper_bgcolor": "#0d1526",
                "plot_bgcolor":  "#0d1526",
                "font":   {"color": "#c8d6f0", "family": "JetBrains Mono, monospace", "size": 11},
                "xaxis":  {"gridcolor": "#1e2d4a", "showgrid": True, "title": "Time"},
                "yaxis":  {"gridcolor": "#1e2d4a", "showgrid": True, "title": "Temperature (°C)"},
                "margin": {"l": 50, "r": 20, "t": 20, "b": 50},
                "annotations": [{
                    "text": "No data yet",
                    "xref": "paper", "yref": "paper",
                    "x": 0.5, "y": 0.5, "showarrow": False,
                    "font": {"color": "#3d5a7a", "size": 14},
                }]
            }
        }).style("width:100%;height:320px;border:1px solid #1a2a42;border-radius:10px;margin-bottom:20px")

        # Raw log
        ui.html('<div class="section-head">Serial Log</div>')
        log_textarea = ui.textarea(label="").style("width:100%;height:160px;margin-bottom:24px")
        log_textarea.props("readonly outlined dark")

# ── Start background tasks (module level — runs once at startup) ──────────────
app.on_startup(lambda: asyncio.ensure_future(serial_reader()))
app.on_startup(lambda: asyncio.ensure_future(ui_updater()))

# ── Entry point ───────────────────────────────────────────────────────────────
ui.run(
    title="IoT Mesh Dashboard",
    dark=True,
    port=8080,
    reload=False,
    favicon="📡",
)