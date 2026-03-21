"""
IoT Sensor Network Dashboard  — Search & Rescue Edition
AHRS Instrument Panel + Radar + Sensor Cards
Pico 2 JSON format (extended):
  {"id":1, "pitch":-12.3, "roll":4.5, "yaw":237.0,
   "temp":24.2, "humidity":45, "rssi":-55,
   "alt":142.0, "heading":237.0, "ax":0.02, "ay":-0.01, "az":9.81}
Minimum required fields: id, rssi
All other fields default gracefully to 0 / neutral.
"""

import asyncio
import json
import csv
import os
from datetime import datetime
from collections import deque
import serial
import serial.tools.list_ports
from nicegui import ui, app

# ── Configuration ─────────────────────────────────────────────────────────────
BAUD_RATE     = 115200
CSV_FILENAME  = "sensor_log.csv"
POLL_INTERVAL = 0.05

# ── Shared state ──────────────────────────────────────────────────────────────
sensor_data: dict[int, dict] = {}
active_node: int | None      = None

serial_port:    serial.Serial | None      = None
logging_active: bool                      = False
csv_writer_obj: csv.DictWriter | None     = None
csv_file_handle                           = None
status_message: str                       = "Disconnected"
raw_log_lines:  deque                     = deque(maxlen=200)

# UI handles
cards_container  = None
instruments_html = None
status_label     = None
log_textarea     = None
port_select      = None
csv_toggle_btn   = None

# ── Serial helpers ─────────────────────────────────────────────────────────────
def list_serial_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]

def open_serial(port: str) -> str | None:
    global serial_port, status_message
    if serial_port and serial_port.is_open:
        serial_port.close()
    try:
        serial_port = serial.Serial(port, BAUD_RATE, timeout=0)
        status_message = f"Connected — {port} @ {BAUD_RATE} baud"
        return None
    except serial.SerialException as e:
        err = str(e)
        if "PermissionError" in err or "Access is denied" in err or "busy" in err.lower():
            status_message = f"Port busy / permission denied: {port}"
            return f"Port {port} is busy. Close other terminals / IDEs using it."
        status_message = f"Failed: {err}"
        return f"Could not open {port}: {err}"

def close_serial():
    global serial_port, status_message
    if serial_port and serial_port.is_open:
        serial_port.close()
    serial_port = None
    status_message = "Disconnected"

def parse_line(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if "id" in data else None
    except json.JSONDecodeError:
        return None

# ── CSV logging ───────────────────────────────────────────────────────────────
def start_csv_logging():
    global csv_writer_obj, csv_file_handle, logging_active
    file_exists = os.path.isfile(CSV_FILENAME)
    csv_file_handle = open(CSV_FILENAME, "a", newline="")
    fieldnames = ["timestamp","id","pitch","roll","yaw","heading","alt",
                  "temp","humidity","rssi","ax","ay","az"]
    csv_writer_obj = csv.DictWriter(csv_file_handle, fieldnames=fieldnames, extrasaction="ignore")
    if not file_exists:
        csv_writer_obj.writeheader()
    logging_active = True

def stop_csv_logging():
    global csv_writer_obj, csv_file_handle, logging_active
    if csv_file_handle:
        csv_file_handle.close()
    csv_writer_obj = csv_file_handle = None
    logging_active = False

def log_row(data: dict):
    if logging_active and csv_writer_obj:
        row = {**data, "timestamp": datetime.now().isoformat(timespec="seconds")}
        csv_writer_obj.writerow(row)
        csv_file_handle.flush()

# ── Serial reader ─────────────────────────────────────────────────────────────
async def serial_reader():
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
                        if logging_active:
                            log_row(parsed)
        except serial.SerialException:
            close_serial()

# ── Instrument panel HTML + JS ────────────────────────────────────────────────
INSTRUMENTS_HTML = r"""
<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;">

  <!-- ARTIFICIAL HORIZON -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
    <div class="inst-label">ARTIFICIAL HORIZON</div>
    <canvas id="ahCanvas" width="220" height="220" class="inst-round"></canvas>
    <div style="display:flex;gap:20px;margin-top:2px;">
      <div class="readout-col">
        <div class="readout-label">PITCH</div>
        <div id="pitchVal" class="readout-val" style="color:#ff9f43;">0.0°</div>
      </div>
      <div class="readout-col">
        <div class="readout-label">ROLL</div>
        <div id="rollVal" class="readout-val" style="color:#3d8bff;">0.0°</div>
      </div>
    </div>
  </div>

  <!-- COMPASS / HSI -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
    <div class="inst-label">COMPASS · HSI</div>
    <canvas id="compassCanvas" width="220" height="220" class="inst-round" style="box-shadow:0 0 20px rgba(61,139,255,0.1);"></canvas>
    <div class="readout-col" style="margin-top:2px;">
      <div class="readout-label">HEADING / YAW</div>
      <div id="headingVal" class="readout-val" style="color:#00d4aa;">000°</div>
    </div>
  </div>

  <!-- RADAR -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
    <div class="inst-label">RADAR · NODE MAP</div>
    <canvas id="radarCanvas" width="220" height="220" class="inst-round" style="box-shadow:0 0 20px rgba(0,212,170,0.15);"></canvas>
    <div class="readout-col" style="margin-top:2px;">
      <div class="readout-label">NODES DETECTED</div>
      <div id="nodeCountVal" class="readout-val" style="color:#00d4aa;">0</div>
    </div>
  </div>

  <!-- VERTICAL INSTRUMENTS -->
  <div style="display:flex;flex-direction:column;gap:14px;">
    <!-- Altitude tape -->
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;">
      <div class="inst-label">ALTITUDE</div>
      <canvas id="altCanvas" width="76" height="180" class="inst-rect"></canvas>
      <div id="altVal" class="readout-val" style="color:#e8f0ff;font-size:.9rem;">0.0 m</div>
    </div>
    <!-- RSSI bar -->
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;">
      <div class="inst-label">SIGNAL</div>
      <canvas id="rssiCanvas" width="76" height="100" class="inst-rect"></canvas>
      <div id="rssiVal" class="readout-val" style="color:#e8f0ff;font-size:.9rem;">– dBm</div>
    </div>
  </div>

  <!-- ACCEL VECTOR -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
    <div class="inst-label">ACCELEROMETER</div>
    <canvas id="accelCanvas" width="160" height="160" class="inst-rect" style="border-radius:8px;"></canvas>
    <div style="display:flex;gap:12px;margin-top:2px;">
      <div class="readout-col"><div class="readout-label">AX</div><div id="axVal" class="readout-val" style="color:#ff4757;font-size:.9rem;">0.00</div></div>
      <div class="readout-col"><div class="readout-label">AY</div><div id="ayVal" class="readout-val" style="color:#7ecb20;font-size:.9rem;">0.00</div></div>
      <div class="readout-col"><div class="readout-label">AZ</div><div id="azVal" class="readout-val" style="color:#3d8bff;font-size:.9rem;">9.81</div></div>
    </div>
  </div>

</div>

<style>
.inst-label  { font-size:.65rem;color:#7a8fad;letter-spacing:.13em;font-family:'JetBrains Mono',monospace; }
.inst-round  { border-radius:50%;border:2px solid #1a2a42;box-shadow:0 0 20px rgba(0,212,170,.08); }
.inst-rect   { border:1px solid #1a2a42; }
.readout-col { display:flex;flex-direction:column;align-items:center;gap:2px; }
.readout-label{ font-size:.58rem;color:#7a8fad;letter-spacing:.1em;font-family:'JetBrains Mono',monospace; }
.readout-val { font-size:1.05rem;font-weight:700;font-family:'JetBrains Mono',monospace; }
</style>
"""

# ── Instrument JS (must use ui.add_body_html — ui.html rejects <script> tags) ──
INSTRUMENTS_JS = """<script>
const DEG = Math.PI / 180;
let state = { pitch:0, roll:0, yaw:0, heading:0, alt:0, rssi:-100, ax:0, ay:0, az:9.81, nodes:{} };
const COLORS = ['#00d4aa','#3d8bff','#ffcc00','#ff9f43','#ff4757','#7ecb20','#a29bfe','#fd79a8'];

// ── Artificial Horizon ──────────────────────────────────────────────────────
function drawAH(pitch, roll) {
  const c = document.getElementById('ahCanvas'); if(!c) return;
  const ctx = c.getContext('2d'), W=c.width, H=c.height, cx=W/2, cy=H/2, R=W/2-2;
  ctx.save(); ctx.clearRect(0,0,W,H);
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.clip();
  ctx.translate(cx,cy); ctx.rotate(-roll*DEG); ctx.translate(-cx,-cy);
  const pxPer = 3.5, off = pitch * pxPer;
  // Sky gradient
  const sky = ctx.createLinearGradient(0,0,0,cy+off);
  sky.addColorStop(0,'#061a3a'); sky.addColorStop(1,'#0a3060');
  ctx.fillStyle=sky; ctx.fillRect(0,0,W,cy+off);
  // Ground gradient
  const gnd = ctx.createLinearGradient(0,cy+off,0,H);
  gnd.addColorStop(0,'#3d1800'); gnd.addColorStop(1,'#1a0800');
  ctx.fillStyle=gnd; ctx.fillRect(0,cy+off,W,H);
  // Horizon
  ctx.strokeStyle='#00d4aa'; ctx.lineWidth=2;
  ctx.beginPath(); ctx.moveTo(0,cy+off); ctx.lineTo(W,cy+off); ctx.stroke();
  // Pitch ladder
  for(let p=-40;p<=40;p+=5) {
    if(p===0) continue;
    const y=cy+off-p*pxPer, len=(p%10===0)?38:22;
    ctx.strokeStyle='rgba(255,255,255,'+(p%10===0?.6:.35)+')';
    ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(cx-len,y); ctx.lineTo(cx+len,y); ctx.stroke();
    if(p%10===0) {
      ctx.fillStyle='rgba(255,255,255,.55)'; ctx.font='10px JetBrains Mono,monospace';
      ctx.fillText(Math.abs(p), cx+len+4, y+4);
      ctx.fillText(Math.abs(p), cx-len-24, y+4);
    }
  }
  ctx.restore();
  // Fixed aircraft symbol
  ctx.strokeStyle='#ffcc00'; ctx.lineWidth=3; ctx.lineCap='round';
  ctx.beginPath(); ctx.moveTo(cx-60,cy); ctx.lineTo(cx-22,cy); ctx.lineTo(cx-22,cy+12); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx+60,cy); ctx.lineTo(cx+22,cy); ctx.lineTo(cx+22,cy+12); ctx.stroke();
  ctx.fillStyle='#ffcc00'; ctx.beginPath(); ctx.arc(cx,cy,4,0,Math.PI*2); ctx.fill();
  // Roll arc
  ctx.save(); ctx.translate(cx,cy);
  ctx.strokeStyle='rgba(255,255,255,.25)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.arc(0,0,R-10,-Math.PI*.75,-Math.PI*.25); ctx.stroke();
  // Roll tick marks
  [10,20,30,45,60].forEach(a=>[a,-a].forEach(ang=>{
    ctx.save(); ctx.rotate(ang*DEG);
    ctx.strokeStyle='rgba(255,255,255,.4)'; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(0,-(R-14)); ctx.lineTo(0,-(R-21)); ctx.stroke();
    ctx.restore();
  }));
  // Roll pointer
  ctx.save(); ctx.rotate(-roll*DEG);
  ctx.fillStyle='#ffcc00';
  ctx.beginPath(); ctx.moveTo(0,-(R-10)); ctx.lineTo(-6,-(R-19)); ctx.lineTo(6,-(R-19)); ctx.closePath(); ctx.fill();
  ctx.restore(); ctx.restore();
  // Bezel
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2);
  ctx.strokeStyle='#1a2a42'; ctx.lineWidth=4; ctx.stroke();
}

// ── Compass / HSI ───────────────────────────────────────────────────────────
function drawCompass(heading) {
  const c = document.getElementById('compassCanvas'); if(!c) return;
  const ctx = c.getContext('2d'), W=c.width, H=c.height, cx=W/2, cy=H/2, R=W/2-4;
  ctx.clearRect(0,0,W,H);
  // Background
  const bg = ctx.createRadialGradient(cx,cy,0,cx,cy,R);
  bg.addColorStop(0,'#050d1c'); bg.addColorStop(1,'#020810');
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.fillStyle=bg; ctx.fill();
  // Rotating rose
  ctx.save(); ctx.translate(cx,cy); ctx.rotate(-heading*DEG);
  // Grid rings
  [.3,.55,.75,.9,1].forEach(f=>{
    ctx.beginPath(); ctx.arc(0,0,R*f,0,Math.PI*2);
    ctx.strokeStyle=`rgba(0,212,170,${f*.12})`; ctx.lineWidth=1; ctx.stroke();
  });
  // Degree ticks
  for(let i=0;i<360;i+=5) {
    ctx.save(); ctx.rotate(i*DEG);
    const isMaj=i%10===0, isCard=i%90===0, isSub=i%30===0;
    const inner = isCard ? R*.78 : isMaj ? R*.83 : R*.9;
    ctx.strokeStyle = isCard ? '#00d4aa' : isSub ? '#3d8bff' : 'rgba(255,255,255,.22)';
    ctx.lineWidth   = isCard ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(0,-(R-1)); ctx.lineTo(0,-inner); ctx.stroke();
    ctx.restore();
  }
  // Degree numbers every 10°
  ctx.font='9px JetBrains Mono,monospace'; ctx.textAlign='center'; ctx.textBaseline='middle';
  for(let i=0;i<360;i+=10) {
    if(i%30===0) continue; // cardinals handle these
    ctx.save(); ctx.rotate(i*DEG);
    ctx.fillStyle='rgba(255,255,255,.3)';
    ctx.fillText(i, 0, -(R*.73));
    ctx.restore();
  }
  // Cardinals
  ctx.font='bold 14px Space Grotesk,sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
  [{d:0,l:'N',c:'#ff4757'},{d:90,l:'E',c:'#e8f0ff'},{d:180,l:'S',c:'#e8f0ff'},{d:270,l:'W',c:'#e8f0ff'}]
    .forEach(({d,l,c})=>{
      ctx.save(); ctx.rotate(d*DEG); ctx.fillStyle=c; ctx.fillText(l, 0, -(R*.68)); ctx.restore();
    });
  [{d:45,l:'NE'},{d:135,l:'SE'},{d:225,l:'SW'},{d:315,l:'NW'}].forEach(({d,l})=>{
    ctx.save(); ctx.rotate(d*DEG);
    ctx.font='10px JetBrains Mono,monospace'; ctx.fillStyle='rgba(255,255,255,.35)';
    ctx.fillText(l, 0, -(R*.68)); ctx.restore();
  });
  ctx.restore(); // end rotating
  // Fixed heading bug
  ctx.save(); ctx.translate(cx,cy);
  ctx.fillStyle='#ffcc00';
  ctx.beginPath(); ctx.moveTo(0,-(R-1)); ctx.lineTo(-8,-(R-14)); ctx.lineTo(8,-(R-14)); ctx.closePath(); ctx.fill();
  ctx.strokeStyle='rgba(255,204,0,.25)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(0,-R*.28); ctx.lineTo(0,-(R-16)); ctx.stroke();
  // Centre
  ctx.fillStyle='#00d4aa'; ctx.beginPath(); ctx.arc(0,0,5,0,Math.PI*2); ctx.fill();
  ctx.strokeStyle='rgba(0,212,170,.3)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(-R*.2,0); ctx.lineTo(R*.2,0); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0,-R*.2); ctx.lineTo(0,R*.2); ctx.stroke();
  ctx.restore();
  // Bezel
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.strokeStyle='#1a2a42'; ctx.lineWidth=4; ctx.stroke();
}

// ── Radar ───────────────────────────────────────────────────────────────────
let radarAngle=0;
function drawRadar(nodes) {
  const c = document.getElementById('radarCanvas'); if(!c) return;
  const ctx = c.getContext('2d'), W=c.width, H=c.height, cx=W/2, cy=H/2, R=W/2-4;
  ctx.clearRect(0,0,W,H);
  // BG
  const bg = ctx.createRadialGradient(cx,cy,0,cx,cy,R);
  bg.addColorStop(0,'#020d06'); bg.addColorStop(1,'#010804');
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.fillStyle=bg; ctx.fill();
  // Rings
  [.25,.5,.75,1].forEach(f=>{
    ctx.beginPath(); ctx.arc(cx,cy,R*f,0,Math.PI*2);
    ctx.strokeStyle=`rgba(0,212,170,${.1+f*.1})`; ctx.lineWidth=1; ctx.stroke();
  });
  // Cross
  ctx.strokeStyle='rgba(0,212,170,.12)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(cx-R,cy); ctx.lineTo(cx+R,cy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx,cy-R); ctx.lineTo(cx,cy+R); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx-R*.7,cy-R*.7); ctx.lineTo(cx+R*.7,cy+R*.7); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx+R*.7,cy-R*.7); ctx.lineTo(cx-R*.7,cy+R*.7); ctx.stroke();
  // Sweep trail
  ctx.save(); ctx.translate(cx,cy); ctx.rotate(radarAngle*DEG);
  for(let i=0;i<60;i++) {
    const a=i*DEG*1.8;
    ctx.beginPath(); ctx.moveTo(0,0); ctx.arc(0,0,R,-a,0); ctx.closePath();
    ctx.fillStyle=`rgba(0,212,170,${(60-i)/60*0.35*(i<3?1:0.04)})`;
    ctx.fill();
  }
  ctx.strokeStyle='rgba(0,212,170,.95)'; ctx.lineWidth=2;
  ctx.beginPath(); ctx.moveTo(0,0); ctx.lineTo(0,-R); ctx.stroke();
  // Glow at tip
  ctx.shadowColor='#00d4aa'; ctx.shadowBlur=12;
  ctx.fillStyle='#00d4aa'; ctx.beginPath(); ctx.arc(0,-R+4,3,0,Math.PI*2); ctx.fill();
  ctx.shadowBlur=0;
  ctx.restore();
  // Blips
  Object.entries(nodes).forEach(([id,n],idx)=>{
    const rssi=n.rssi??-85, hdg=n.heading??0;
    const norm=Math.min(1,Math.max(0,(-rssi-55)/45));
    const br=R*(0.12+norm*0.84);
    const ang=(hdg-90)*DEG;
    const bx=cx+br*Math.cos(ang), by=cy+br*Math.sin(ang);
    const col=COLORS[idx%COLORS.length];
    // Blip
    ctx.save(); ctx.shadowColor=col; ctx.shadowBlur=14;
    ctx.fillStyle=col; ctx.beginPath(); ctx.arc(bx,by,5,0,Math.PI*2); ctx.fill();
    // Ping ring
    ctx.strokeStyle=col; ctx.lineWidth=1; ctx.globalAlpha=0.3;
    ctx.beginPath(); ctx.arc(bx,by,10,0,Math.PI*2); ctx.stroke();
    ctx.restore();
    ctx.fillStyle='rgba(255,255,255,.65)'; ctx.font='9px JetBrains Mono,monospace';
    ctx.fillText(`N${id}`,bx+8,by-5);
  });
  // Centre
  ctx.fillStyle='#00d4aa'; ctx.beginPath(); ctx.arc(cx,cy,4,0,Math.PI*2); ctx.fill();
  // Range labels
  ctx.fillStyle='rgba(0,212,170,.35)'; ctx.font='8px JetBrains Mono,monospace';
  ctx.fillText('25',cx+R*.25+3,cy-2); ctx.fillText('50',cx+R*.5+3,cy-2);
  ctx.fillText('75',cx+R*.75+3,cy-2); ctx.fillText('m',cx+R-12,cy-2);
  // Bezel
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.strokeStyle='#1a2a42'; ctx.lineWidth=4; ctx.stroke();
}

// ── Altitude tape ───────────────────────────────────────────────────────────
function drawAlt(alt) {
  const c=document.getElementById('altCanvas'); if(!c) return;
  const ctx=c.getContext('2d'), W=c.width, H=c.height;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#050c1a'; ctx.fillRect(0,0,W,H);
  const pxPer5=17, mid=H/2;
  ctx.font='8px JetBrains Mono,monospace';
  for(let a=Math.floor((alt-H/pxPer5*5)/5)*5; a<=alt+H/pxPer5*5; a+=5) {
    const y=mid-(a-alt)/5*pxPer5;
    if(y<0||y>H) continue;
    const maj=a%10===0;
    ctx.strokeStyle=maj?'rgba(255,255,255,.5)':'rgba(255,255,255,.18)';
    ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(maj?W*.35:W*.55,y); ctx.lineTo(W,y); ctx.stroke();
    if(maj) { ctx.fillStyle='rgba(255,255,255,.45)'; ctx.fillText(a+'m',1,y+3); }
  }
  // Bug
  ctx.fillStyle='#00d4aa';
  ctx.beginPath(); ctx.moveTo(W,mid-8); ctx.lineTo(W*.55,mid); ctx.lineTo(W,mid+8); ctx.closePath(); ctx.fill();
  ctx.strokeStyle='rgba(0,212,170,.4)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(0,mid); ctx.lineTo(W*.55,mid); ctx.stroke();
}

// ── RSSI bar ────────────────────────────────────────────────────────────────
function drawRSSI(rssi) {
  const c=document.getElementById('rssiCanvas'); if(!c) return;
  const ctx=c.getContext('2d'), W=c.width, H=c.height;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#050c1a'; ctx.fillRect(0,0,W,H);
  const norm=Math.min(1,Math.max(0,(rssi+100)/60));
  const barH=norm*(H-18);
  const col=norm>.67?'#00d4aa':norm>.33?'#f5a623':'#ff4757';
  const g=ctx.createLinearGradient(0,H,0,H-barH);
  g.addColorStop(0,col); g.addColorStop(1,col+'33');
  ctx.fillStyle=g; ctx.fillRect(10,H-9-barH,W-20,barH);
  ctx.shadowColor=col; ctx.shadowBlur=10;
  ctx.fillStyle=col; ctx.fillRect(10,H-9-barH,W-20,2); ctx.shadowBlur=0;
  // Ticks
  [-100,-80,-60,-40].forEach(v=>{
    const n=(v+100)/60, y=H-9-n*(H-18);
    ctx.strokeStyle='rgba(255,255,255,.2)'; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(7,y); ctx.lineTo(10,y); ctx.stroke();
    ctx.fillStyle='rgba(255,255,255,.3)'; ctx.font='7px JetBrains Mono,monospace';
    ctx.fillText(v,0,y+3);
  });
}

// ── Accelerometer XY ────────────────────────────────────────────────────────
function drawAccel(ax,ay,az) {
  const c=document.getElementById('accelCanvas'); if(!c) return;
  const ctx=c.getContext('2d'), W=c.width, H=c.height, cx=W/2, cy=H/2;
  ctx.clearRect(0,0,W,H); ctx.fillStyle='#050c1a'; ctx.fillRect(0,0,W,H);
  const scale=14;
  // Grid
  ctx.strokeStyle='rgba(255,255,255,.06)'; ctx.lineWidth=1;
  for(let i=-4;i<=4;i++){
    ctx.beginPath(); ctx.moveTo(cx+i*scale*2,0); ctx.lineTo(cx+i*scale*2,H); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0,cy+i*scale*2); ctx.lineTo(W,cy+i*scale*2); ctx.stroke();
  }
  // Axes
  ctx.strokeStyle='rgba(255,255,255,.18)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(0,cy); ctx.lineTo(W,cy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx,0); ctx.lineTo(cx,H); ctx.stroke();
  ctx.fillStyle='rgba(255,255,255,.25)'; ctx.font='9px JetBrains Mono,monospace';
  ctx.fillText('+AX',W-26,cy-4); ctx.fillText('+AY',cx+4,10);
  // AZ circle (magnitude of vertical)
  const azN=Math.min(2,Math.abs(az))/2;
  ctx.save(); ctx.shadowColor='#3d8bff'; ctx.shadowBlur=14;
  ctx.strokeStyle=`rgba(61,139,255,${.25+azN*.65})`; ctx.lineWidth=2;
  ctx.beginPath(); ctx.arc(cx,cy,azN*32,0,Math.PI*2); ctx.stroke(); ctx.restore();
  // XY vector arrow
  const vx=ax*scale, vy=-ay*scale;
  const len=Math.sqrt(vx*vx+vy*vy);
  if(len>0.5) {
    ctx.save(); ctx.shadowColor='#ff4757'; ctx.shadowBlur=14;
    ctx.strokeStyle='#ff4757'; ctx.lineWidth=2.5;
    ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+vx,cy+vy); ctx.stroke();
    // Arrowhead
    const ang=Math.atan2(vy,vx);
    ctx.fillStyle='#ff4757';
    ctx.beginPath();
    ctx.moveTo(cx+vx,cy+vy);
    ctx.lineTo(cx+vx-10*Math.cos(ang-0.4),cy+vy-10*Math.sin(ang-0.4));
    ctx.lineTo(cx+vx-10*Math.cos(ang+0.4),cy+vy-10*Math.sin(ang+0.4));
    ctx.closePath(); ctx.fill(); ctx.restore();
  }
  // Origin
  ctx.fillStyle='rgba(255,255,255,.5)'; ctx.beginPath(); ctx.arc(cx,cy,3,0,Math.PI*2); ctx.fill();
  ctx.fillStyle='rgba(61,139,255,.5)'; ctx.font='9px JetBrains Mono,monospace';
  ctx.fillText(`|AZ| ${Math.abs(az).toFixed(2)}`,cx-28,cy+42);
}

// ── Master render ────────────────────────────────────────────────────────────
function renderAll(s) {
  drawAH(s.pitch||0, s.roll||0);
  drawCompass(s.heading??s.yaw??0);
  drawRadar(s.nodes||{});
  drawAlt(s.alt||0);
  drawRSSI(s.rssi??-100);
  drawAccel(s.ax||0, s.ay||0, s.az??9.81);
  const $=id=>document.getElementById(id);
  if($('pitchVal'))    $('pitchVal').textContent   = (s.pitch||0).toFixed(1)+'°';
  if($('rollVal'))     $('rollVal').textContent    = (s.roll||0).toFixed(1)+'°';
  const hdg = s.heading??s.yaw??0;
  if($('headingVal'))  $('headingVal').textContent = String(Math.round((hdg%360+360)%360)).padStart(3,'0')+'°';
  if($('altVal'))      $('altVal').textContent     = (s.alt||0).toFixed(1)+' m';
  if($('rssiVal'))     $('rssiVal').textContent    = (s.rssi??'–')+' dBm';
  if($('axVal'))       $('axVal').textContent      = (s.ax||0).toFixed(2);
  if($('ayVal'))       $('ayVal').textContent      = (s.ay||0).toFixed(2);
  if($('azVal'))       $('azVal').textContent      = (s.az??9.81).toFixed(2);
  if($('nodeCountVal'))$('nodeCountVal').textContent= Object.keys(s.nodes||{}).length;
}

// Radar sweep animation (independent of data)
(function sweep() { radarAngle=(radarAngle+2)%360; drawRadar(state.nodes||{}); requestAnimationFrame(sweep); })();

renderAll(state);

window.updateInstruments = function(newState) {
  Object.assign(state, newState);
  renderAll(state);
};
</script>"""

# ── Card builder ──────────────────────────────────────────────────────────────
def rssi_info(rssi):
    if rssi >= -60: return "Excellent", "#00d4aa"
    if rssi >= -70: return "Good",      "#7ecb20"
    if rssi >= -80: return "Fair",      "#f5a623"
    return "Weak", "#ff4757"

def build_card(container, node_id: int, data: dict, is_active: bool):
    rl, rc = rssi_info(data.get("rssi", -100))
    border = "#00d4aa" if is_active else "#1a2a42"
    shadow = "0 0 20px rgba(0,212,170,0.2)" if is_active else "none"

    with container:
        with ui.card().classes("sensor-card").style(
            f"border-color:{border}!important;box-shadow:{shadow}!important;cursor:pointer"
        ).on("click", lambda nid=node_id: set_active(nid)):
            with ui.row().classes("card-header"):
                ui.label(f"NODE {node_id:02d}").classes("node-id")
                ui.badge(rl).style(f"background:{rc};color:#0a0f1e;font-weight:700;font-size:.68rem")
            if is_active:
                ui.label("▶ INSTRUMENTS ACTIVE").style(
                    "font-size:.58rem;color:#00d4aa;letter-spacing:.09em;margin-top:-6px;margin-bottom:4px"
                )
            with ui.row().classes("metrics-row"):
                for lbl, key, unit, col in [
                    ("PITCH",  "pitch",    "°",    "#ff9f43"),
                    ("ROLL",   "roll",     "°",    "#3d8bff"),
                    ("YAW",    "yaw",      "°",    "#00d4aa"),
                    ("TEMP",   "temp",     "°C",   "#ff9f43"),
                    ("HUM",    "humidity", "%",    "#3d8bff"),
                    ("RSSI",   "rssi",     " dBm", rc),
                ]:
                    val = data.get(key, "–")
                    with ui.column().classes("metric"):
                        ui.label(lbl).classes("metric-label")
                        ui.label((f"{val}{unit}" if val != "–" else "–")).classes("metric-value").style(
                            f"font-size:.9rem;color:{col}"
                        )

def set_active(nid: int):
    global active_node
    active_node = nid

# ── UI updater ────────────────────────────────────────────────────────────────
async def ui_updater():
    global active_node
    while True:
        await asyncio.sleep(0.25)

        # Status label
        if status_label:
            is_conn = serial_port and serial_port.is_open
            status_label.set_text(status_message)
            status_label.style(f"color:{'#00d4aa' if is_conn else '#ff4757'}")

        # Auto-select first node
        if active_node is None and sensor_data:
            active_node = sorted(sensor_data.keys())[0]

        # Sensor cards
        if cards_container:
            cards_container.clear()
            if not sensor_data:
                with cards_container:
                    ui.html('<div class="empty-state">Waiting for sensor data…<br>connect a serial port to begin.</div>')
            else:
                for nid in sorted(sensor_data.keys()):
                    build_card(cards_container, nid, sensor_data[nid], nid == active_node)

        # Push instrument state to browser
        if active_node is not None and active_node in sensor_data:
            d = sensor_data[active_node]
            nodes_map = {
                str(k): {
                    "rssi":    v.get("rssi", -85),
                    "heading": v.get("heading", v.get("yaw", 0)),
                }
                for k, v in sensor_data.items()
            }
            js_state = {
                "pitch":   d.get("pitch",   0),
                "roll":    d.get("roll",    0),
                "yaw":     d.get("yaw",     0),
                "heading": d.get("heading", d.get("yaw", 0)),
                "alt":     d.get("alt",     0),
                "rssi":    d.get("rssi",    -100),
                "ax":      d.get("ax",      0),
                "ay":      d.get("ay",      0),
                "az":      d.get("az",      9.81),
                "nodes":   nodes_map,
            }
            try:
                ui.run_javascript(f"if(window.updateInstruments)window.updateInstruments({json.dumps(js_state)})")
            except Exception:
                pass

        # Serial log
        if log_textarea:
            log_textarea.set_value("\n".join(raw_log_lines))


# ── Page ──────────────────────────────────────────────────────────────────────
@ui.page("/")
def index():
    global cards_container, instruments_html, status_label, log_textarea, port_select, csv_toggle_btn

    ui.add_head_html("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Space+Grotesk:wght@300;500;700&display=swap" rel="stylesheet">
    <style>
      :root{--bg-deep:#070c18;--bg-card:#0d1526;--bg-panel:#111c30;
        --accent-teal:#00d4aa;--accent-blue:#3d8bff;--text-pri:#e8f0ff;--text-sec:#7a8fad;--border:#1a2a42;}
      body,.nicegui-content{background:var(--bg-deep)!important;font-family:'JetBrains Mono',monospace;}
      .topbar{background:linear-gradient(90deg,#070c18 0%,#0d1a32 60%,#070c18 100%);
        border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;gap:16px;}
      .topbar-title{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.25rem;
        color:var(--text-pri);letter-spacing:.04em;}
      .topbar-title span{color:var(--accent-teal);}
      .live-dot{width:8px;height:8px;border-radius:50%;background:var(--accent-teal);
        box-shadow:0 0 6px var(--accent-teal);animation:pulse 1.5s ease-in-out infinite;}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
      .control-strip{background:var(--bg-panel);border-bottom:1px solid var(--border);
        padding:10px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
      .status-chip{font-size:.78rem;padding:4px 12px;border-radius:20px;
        background:var(--bg-deep);border:1px solid var(--border);color:var(--text-sec);}
      .sensor-card{background:var(--bg-card)!important;border:1px solid var(--border)!important;
        border-radius:10px!important;padding:14px!important;min-width:230px;
        transition:border-color .25s,box-shadow .25s;}
      .card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
      .node-id{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:.9rem;
        color:var(--accent-blue);letter-spacing:.1em;}
      .metrics-row{display:flex;gap:10px;flex-wrap:wrap;}
      .metric{display:flex;flex-direction:column;gap:3px;}
      .metric-label{font-size:.58rem;color:var(--text-sec);letter-spacing:.12em;}
      .metric-value{font-size:1rem;font-weight:700;color:var(--text-pri);}
      .section-head{font-family:'Space Grotesk',sans-serif;font-weight:500;font-size:.78rem;
        color:var(--text-sec);letter-spacing:.14em;text-transform:uppercase;
        padding:20px 24px 8px;border-bottom:1px solid var(--border);margin-bottom:16px;}
      textarea{background:var(--bg-deep)!important;color:#4af0b8!important;
        font-family:'JetBrains Mono',monospace!important;font-size:.75rem!important;
        border:1px solid var(--border)!important;border-radius:6px!important;resize:none;}
      .q-btn{font-family:'JetBrains Mono',monospace!important;font-size:.78rem!important;letter-spacing:.06em!important;}
      .empty-state{color:var(--text-sec);font-size:.85rem;text-align:center;
        padding:40px;border:1px dashed var(--border);border-radius:10px;width:100%;letter-spacing:.05em;}
      ::-webkit-scrollbar{width:5px;height:5px;}
      ::-webkit-scrollbar-track{background:var(--bg-deep);}
      ::-webkit-scrollbar-thumb{background:#1e2d4a;border-radius:3px;}
    </style>
    """)

    # Top bar
    with ui.row().classes("topbar").style("width:100%"):
        ui.html('<div class="live-dot"></div>')
        ui.html('<div class="topbar-title">SAR Mesh <span>AHRS</span> Dashboard</div>')
        ui.label("nRF24L01 · Pico 2 Anchor · Search & Rescue").style(
            "color:#3d5a7a;font-size:.75rem;margin-left:auto")

    # Control strip
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

        ui.button("↻", on_click=on_refresh_ports).props("flat dense").style("color:#3d8bff;font-size:1.1rem")

        async def on_connect():
            sel = port_select.value
            if not sel or sel == "(no ports found)":
                ui.notify("Select a valid port first", type="warning"); return
            err = open_serial(sel)
            if err: ui.notify(err, type="negative", multi_line=True, timeout=6000)
            else:   ui.notify(f"Connected to {sel}", type="positive")

        async def on_disconnect():
            close_serial(); ui.notify("Disconnected", type="info")

        ui.button("CONNECT", on_click=on_connect).props("unelevated").style(
            "background:#00d4aa;color:#070c18;font-weight:700")
        ui.button("DISCONNECT", on_click=on_disconnect).props("unelevated").style(
            "background:#1a2a42;color:#7a8fad")

        async def toggle_csv():
            global logging_active
            if logging_active:
                stop_csv_logging()
                csv_toggle_btn.set_text("▶ LOG CSV")
                csv_toggle_btn.style("background:#1a2a42;color:#7a8fad")
                ui.notify(f"Logging stopped → {CSV_FILENAME}", type="info")
            else:
                start_csv_logging()
                csv_toggle_btn.set_text("■ STOP LOG")
                csv_toggle_btn.style("background:#ff4757;color:#fff")
                ui.notify(f"Logging to {CSV_FILENAME}", type="positive")

        csv_toggle_btn = ui.button("▶ LOG CSV", on_click=toggle_csv).props("unelevated").style(
            "background:#1a2a42;color:#7a8fad;margin-left:auto")
        status_label = ui.label(status_message).classes("status-chip").style("color:#ff4757")

    # Content
    with ui.column().style("width:100%;padding:0 24px;box-sizing:border-box;gap:0"):

        # Sensor cards
        ui.html('<div class="section-head">Live Sensor Nodes · Click a card to view its instruments</div>')
        cards_container = ui.row().style("flex-wrap:wrap;gap:12px;padding:0 0 4px;min-height:100px")
        with cards_container:
            ui.html('<div class="empty-state">Waiting for sensor data… connect a serial port to begin.</div>')

        # AHRS panel
        ui.html('<div class="section-head">AHRS · Compass · Radar · Accelerometer</div>')
        instruments_html = ui.html(INSTRUMENTS_HTML).style(
            "width:100%;background:#08101f;border:1px solid #1a2a42;border-radius:10px;"
            "padding:16px 20px;box-sizing:border-box;margin-bottom:20px;overflow-x:auto"
        )
        # Inject instrument JS via add_body_html (ui.html forbids <script> tags)
        ui.add_body_html(INSTRUMENTS_JS)

        # Log
        ui.html('<div class="section-head">Serial Log</div>')
        log_textarea = ui.textarea(label="").style("width:100%;height:150px;margin-bottom:24px")
        log_textarea.props("readonly outlined dark")


# ── Background tasks (module level) ──────────────────────────────────────────
app.on_startup(lambda: asyncio.ensure_future(serial_reader()))
app.on_startup(lambda: asyncio.ensure_future(ui_updater()))

# ── Run ───────────────────────────────────────────────────────────────────────
ui.run(
    title="SAR AHRS Dashboard",
    dark=True,
    port=8080,
    reload=False,
    favicon="🛰️",
)