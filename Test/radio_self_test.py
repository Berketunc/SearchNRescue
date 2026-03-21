from machine import Pin, SPI
from nrf24l01 import NRF24L01
import nrf24l01 as nrfregs
import utime

# Single-Pico radio sanity test (no second Pico required)
# - If init succeeds and register readbacks are stable, the local radio + wiring are OK.
# - TX send is expected to fail without a peer (no ACK).

led = Pin("LED", Pin.OUT)
button = Pin(15, Pin.IN, Pin.PULL_UP)

spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
csn = Pin(14)
ce = Pin(17)


def init_radio(max_attempts=5):
    for i in range(1, max_attempts + 1):
        try:
            r = NRF24L01(
                spi,
                csn,
                ce,
                payload_size=1,
                spi_baudrate=1_000_000,
                startup_delay_ms=120,
            )
            return r
        except OSError as exc:
            print("Init fail (%d/%d): %s" % (i, max_attempts, exc))
            utime.sleep_ms(300)
    return None


nrf = init_radio()
if nrf is None:
    print("RESULT: FAIL (local hardware not detected)")
    raise SystemExit

# Use same pipes as project; only for consistency
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
nrf.open_tx_pipe(pipes[0])
nrf.open_rx_pipe(1, pipes[1])

# Register sanity checks
aw = nrf.reg_read(nrfregs.SETUP_AW)
ch_before = nrf.reg_read(nrfregs.RF_CH)
nrf.set_channel(33)
ch_after = nrf.reg_read(nrfregs.RF_CH)
nrf.set_channel(ch_before)

print("SETUP_AW:", aw)
print("RF_CH before:", ch_before, "after set/read:", ch_after)
print("STATUS:", nrf.read_status())

if aw == 0b11 and ch_after == 33:
    print("RESULT: PASS (radio responds; SPI/CE/CSN/wiring look OK)")
else:
    print("RESULT: WARN (radio detected but register test unexpected)")

print("Press button to attempt TX (expected: send failed/timed out without peer).")
last = 1
while True:
    v = button.value()
    if v != last and v == 0:
        led.toggle()
        try:
            nrf.stop_listening()
            nrf.send(b"\x01", timeout=120)
            print("TX unexpectedly ACKed (peer may be active)")
        except OSError as exc:
            print("TX test without peer:", exc)
    last = v
    utime.sleep_ms(20)
