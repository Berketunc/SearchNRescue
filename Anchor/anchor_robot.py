# anchor_robot.py
# ─────────────────────────────────────────────────────────────
# Run this file instead of Anchor.py when you want joystick
# control.  It reuses every piece of Anchor.py unchanged —
# the radio init, retry logic, and pipe configuration —
# and only adds the joystick read + command encoding on top.
#
# File layout on the Pico:
#   Anchor.py           ← original, untouched
#   anchor_robot.py     ← this file (the launcher)
#   control/
#     __init__.py
#     joystick.py
#   nrf24l01.py
# ─────────────────────────────────────────────────────────────

# 1. Pull in the original Anchor environment verbatim.
#    exec() runs Anchor.py in this module's global namespace so
#    every name it defines (nrf, led, button, configure_radio …)
#    becomes available here without any import changes to Anchor.py.
with open("Anchor.py") as _f:
    exec(_f.read(), globals())

# 2. Joystick layer (new hardware, new logic — no overlap with Anchor.py)
from control.joystick import Joystick, COMMAND_BYTE

stick = Joystick(
    x_pin=26,   # ADC0
    y_pin=27,   # ADC1
    # btn_pin=None  — GP15 is already used by Anchor's push-button
    dead=8000,
)

# 3. Override the main loop with joystick-aware behaviour.
#    The radio, retry logic, and LED from Anchor.py are reused as-is.
print("anchor_robot.py — joystick mode active")

last_cmd_byte = -1

while True:
    cmd_byte = stick.read_byte()      # 0x00–0x05 single byte
    cmd_name = stick.read_command()   # human-readable for serial

    if cmd_byte != last_cmd_byte:
        print(f"Sending: {cmd_name}")

        if nrf is not None:
            nrf.stop_listening()
            try:
                nrf.send(bytes([cmd_byte]))
                last_cmd_byte = cmd_byte
            except OSError as exc:
                msg = str(exc)
                if msg in ("send failed", "timed out"):
                    print("Scout not responding (packet not acknowledged)")
                    last_cmd_byte = cmd_byte   # don't retry same state spam
                else:
                    print("Radio hardware error; entering offline mode")
                    nrf = None                 # triggers background retry from Anchor.py scope
        else:
            last_cmd_byte = cmd_byte
            print(f"  (radio offline — command not sent)")

    # ── Background radio retry (inherited from Anchor.py) ────
    # `nrf`, `next_retry_ms`, and `configure_radio` all live in
    # this namespace because exec() ran Anchor.py here.
    import utime as _utime
    if nrf is None and _utime.ticks_diff(_utime.ticks_ms(), next_retry_ms) >= 0:
        nrf = init_radio(max_attempts=1)
        if nrf is not None:
            configure_radio(nrf)
        next_retry_ms = _utime.ticks_add(_utime.ticks_ms(), 5000)

    _utime.sleep_ms(20)