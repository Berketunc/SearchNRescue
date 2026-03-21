# control/joystick.py
# ─────────────────────────────────────────────────────────────
# Reads an analog joystick and converts raw ADC values into
# a directional command string.
#
# Usage (drop this file on the Pico, then in Anchor.py add):
#
#   from control.joystick import Joystick
#   stick = Joystick()            # default pins ADC0/ADC1
#   cmd = stick.read_command()    # "FORWARD" | "BACK" | etc.
#
# The module is intentionally standalone — Anchor.py does not
# need to be modified; just call these two lines at the top of
# your existing while-True loop.
# ─────────────────────────────────────────────────────────────

from machine import ADC, Pin

# ── Payload map ──────────────────────────────────────────────
# Encodes each command as a single byte so it slots into the
# existing payload_size=1 radio configuration without change.
COMMAND_BYTE = {
    "FORWARD": 0x02,
    "BACK":    0x03,
    "LEFT":    0x04,
    "RIGHT":   0x05,
    "IDLE":    0x00,
}

# Reverse map – lets Scout decode a received byte back to a string
BYTE_COMMAND = {v: k for k, v in COMMAND_BYTE.items()}


class Joystick:
    """
    Analog joystick reader with configurable dead-zone.

    Parameters
    ----------
    x_pin   : int  ADC GP pin number for the X axis  (default 26)
    y_pin   : int  ADC GP pin number for the Y axis  (default 27)
    btn_pin : int  GP pin for the joystick button     (default 15,
                   but your Anchor already uses 15 for a push-button
                   so set btn_pin=None if unused)
    dead    : int  Dead-zone radius around centre     (default 8000)
    centre  : int  Midpoint of the 16-bit ADC range   (default 32768)
    """

    def __init__(
        self,
        x_pin: int = 26,
        y_pin: int = 27,
        btn_pin: int | None = None,
        dead: int = 8000,
        centre: int = 32768,
    ):
        self._x      = ADC(Pin(x_pin))
        self._y      = ADC(Pin(y_pin))
        self._btn    = Pin(btn_pin, Pin.IN, Pin.PULL_UP) if btn_pin is not None else None
        self.dead    = dead
        self.centre  = centre

    # ── Raw reads ────────────────────────────────────────────

    def raw(self) -> tuple[int, int]:
        """Return (raw_x, raw_y) as 16-bit unsigned integers."""
        return self._x.read_u16(), self._y.read_u16()

    def button_pressed(self) -> bool:
        """True while the joystick button is held down."""
        return self._btn is not None and self._btn.value() == 0

    # ── Command ──────────────────────────────────────────────

    def read_command(self) -> str:
        """
        Map joystick position to a command string.

        Y axis takes priority over X (push forward wins over diagonal).
        Returns one of: "FORWARD", "BACK", "LEFT", "RIGHT", "IDLE"
        """
        rx, ry = self.raw()
        dy = ry - self.centre
        dx = rx - self.centre

        if   dy < -self.dead:  return "FORWARD"
        elif dy >  self.dead:  return "BACK"
        elif dx < -self.dead:  return "LEFT"
        elif dx >  self.dead:  return "RIGHT"
        else:                  return "IDLE"

    def read_byte(self) -> int:
        """Return the command as a single byte (fits payload_size=1)."""
        return COMMAND_BYTE[self.read_command()]