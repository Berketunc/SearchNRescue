# scout_robot.py  —  full autonomous brain
# ─────────────────────────────────────────────────────────────
# Run this instead of Scout.py.
# Scout.py is left completely untouched; this file loads its
# radio/LED setup via exec(), then runs its own loop that:
#   1. Does a radar sweep every cycle
#   2. Reads IMU orientation
#   3. Decides the best action autonomously
#   4. Checks if Anchor sent a joystick override (override wins)
#   5. Drives motors
#   6. Sends a compact JSON telemetry packet to serial
#      (the PC dashboard reads this passively)
#
# File layout on Scout Pico:
#   Scout.py
#   scout_robot.py          <- run this
#   control/
#     __init__.py
#     motors.py
#     safety.py
#     radar.py
#     imu.py
#     joystick.py           (for BYTE_COMMAND decode table)
#   nrf24l01.py
# ─────────────────────────────────────────────────────────────

import utime as _utime
import ujson
import os

# ── 1. Load Scout.py setup (radio, LED, pipes) without its loop
_scout_lines = []
_scout_candidates = [
    os.path.join(os.path.dirname(__file__), "scout.py"),
    os.path.join(os.path.dirname(__file__), "Scout.py"),
    "scout.py",
    "Scout.py",
]
_scout_file = next((p for p in _scout_candidates if os.path.exists(p)), None)
if _scout_file is None:
    raise OSError("scout.py not found beside scout_robot.py")

with open(_scout_file) as _f:
    for _line in _f:
        if _line.strip().startswith("while True:"):
            break
        _scout_lines.append(_line)
exec("".join(_scout_lines), globals())
# nrf, init_radio, next_retry_ms, pipes now live here

# Resolve names injected by Scout bootstrap explicitly.
init_radio = globals().get("init_radio")
nrf = globals().get("nrf")
next_retry_ms = globals().get("next_retry_ms")
pipes = globals().get("pipes")

if init_radio is None:
    raise RuntimeError("Scout bootstrap missing init_radio")
if next_retry_ms is None or pipes is None:
    raise RuntimeError("Scout bootstrap missing next_retry_ms/pipes")

# ── 2. Hardware modules
from control.radar    import Radar
from control.imu      import IMU
from control.motors   import MotorPair
from control.joystick import BYTE_COMMAND

radar = Radar(
    servo_pin=0,    # GP0  — servo signal
    trig_pin=2,     # GP2  — HC-SR04 TRIG
    echo_pin=3,     # GP3  — HC-SR04 ECHO
    min_angle=0,
    max_angle=180,
    step=15,        # 13 measurement points per sweep
    settle_ms=60,   # shorter settle = faster sweep
)

imu = IMU(
    sda_pin=4,      # GP4  — I2C0 SDA
    scl_pin=5,      # GP5  — I2C0 SCL
)

motors = MotorPair(
    a_in1=8,  a_in2=9,  a_pwm=10,
    b_in1=11, b_in2=12, b_pwm=13,
)

# ── 3. Autonomy constants
SAFE_DIST_CM    = 30.0   # minimum forward clearance to drive ahead
TURN_DIST_CM    = 20.0   # if nearest obstacle < this, must turn away
TILT_LIMIT_DEG  = 35.0   # stop if robot is tipped this far
LOOP_MS         = 20     # target loop period (radar sweep dominates)

# ── 4. Override priority
# When Anchor sends a non-IDLE joystick byte the human takes over
# completely for OVERRIDE_TTL_MS before autonomy resumes.
OVERRIDE_TTL_MS  = 500
_override_until_ms = 0
_override_cmd      = "IDLE"


def _check_override():
    global nrf
    if nrf is None or not nrf.any():
        return None
    cmd = None
    while nrf.any():
        buf  = nrf.recv()
        byte = buf[0]
        if byte in BYTE_COMMAND:
            cmd = BYTE_COMMAND[byte]
        elif byte == 1:
            cmd = "FORWARD"
        else:
            cmd = "IDLE"
    return cmd


# ── 5. Autonomous decision

def _decide(scan, imu_data):
    """
    Decide what to do based on radar scan and IMU data.
    Returns: FORWARD / BACK / LEFT / RIGHT / IDLE
    """
    if abs(imu_data["pitch"]) > TILT_LIMIT_DEG or abs(imu_data["roll"]) > TILT_LIMIT_DEG:
        return "IDLE"

    if not scan:
        return "IDLE"

    forward_clear = radar.clear_arc(scan, min_dist_cm=SAFE_DIST_CM,
                                    min_angle=60, max_angle=120)
    if forward_clear:
        return "FORWARD"

    left_min  = min((d for a, d in scan if   0 <= a <  60), default=0.0)
    right_min = min((d for a, d in scan if 120 < a <= 180), default=0.0)
    _, nearest_dist = radar.nearest(scan)

    if nearest_dist < TURN_DIST_CM:
        return "BACK"

    return "LEFT" if left_min > right_min else "RIGHT"


# ── 6. Telemetry packet

def _telemetry(scan, imu_data, action, override_active):
    """
    Print one JSON line to serial — PC dashboard reads this passively.
    {"r":[[angle,dist],...],"p":pitch,"ro":roll,"gz":gz,"n":[angle,dist],"cmd":"FWD","ov":0}
    """
    nearest_a, nearest_d = radar.nearest(scan) if scan else (90, 999)
    payload = {
        "r":   [[a, min(d, 400.0)] for a, d in scan],
        "p":   imu_data["pitch"],
        "ro":  imu_data["roll"],
        "gz":  imu_data["heading_change"],
        "n":   [nearest_a, nearest_d],
        "cmd": action,
        "ov":  1 if override_active else 0,
    }
    print(ujson.dumps(payload))


# ── 7. Main loop

print("scout_robot.py — autonomous mode active")
motors.stop()

while True:
    t_loop = _utime.ticks_ms()

    # a. Radar sweep (blocks ~0.8 s for 13 steps x 60 ms)
    scan = radar.sweep()

    # b. IMU read
    try:
        imu_data = imu.read()
    except OSError:
        imu_data = {"pitch": 0.0, "roll": 0.0, "heading_change": 0.0}

    # c. Joystick override check
    incoming = _check_override()
    now_ms   = _utime.ticks_ms()

    if incoming is not None and incoming != "IDLE":
        _override_cmd      = incoming
        _override_until_ms = _utime.ticks_add(now_ms, OVERRIDE_TTL_MS)

    override_active = _utime.ticks_diff(_override_until_ms, now_ms) > 0
    action = _override_cmd if override_active else _decide(scan, imu_data)

    # d. Motor output
    motors.execute(action)
    # e. Telemetry -> serial -> PC dashboard
    _telemetry(scan, imu_data, action, override_active)

    # f. Background radio retry
    if nrf is None and _utime.ticks_diff(now_ms, next_retry_ms) >= 0:
        nrf = init_radio(max_attempts=1)
        if nrf is not None:
            nrf.open_rx_pipe(1, pipes[0])
            nrf.open_tx_pipe(pipes[1])
            nrf.start_listening()
        next_retry_ms = _utime.ticks_add(now_ms, 5000)

    # g. Pace
    elapsed = _utime.ticks_diff(_utime.ticks_ms(), t_loop)
    _utime.sleep_ms(max(0, LOOP_MS - elapsed))