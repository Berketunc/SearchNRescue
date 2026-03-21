from machine import Pin, SPI, time_pulse_us
from nrf24l01 import NRF24L01
import utime

# 1. Setup LED and HC-SR04
led = Pin("LED", Pin.OUT)
trig = Pin(3, Pin.OUT)   # TRIG on GP6
echo = Pin(2, Pin.IN)    # ECHO on GP7
trig.value(0)
utime.sleep_ms(2)

# 2. Setup SPI and Radio
spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cfg = {"csn": 14, "ce": 17}


def init_radio(max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        try:
            return NRF24L01(
                spi,
                Pin(cfg["csn"]),
                Pin(cfg["ce"]),
                payload_size=16,
                spi_baudrate=1_000_000,
                startup_delay_ms=120,
            )
        except OSError as exc:
            print(f"Radio init failed ({attempt}/{max_attempts}): {exc}")
            utime.sleep_ms(300)

    print(
        "nRF24L01 unavailable. Running without radio. Verify: VCC=3.3V, GND, SCK=GP18, MOSI=GP19, MISO=GP16, CSN=GP14, CE=GP17."
    )
    return None


nrf = init_radio()
next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

# Communication Pipes
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")


def configure_radio(radio):
    radio.open_tx_pipe(pipes[0])
    radio.open_rx_pipe(1, pipes[1])


if nrf is not None:
    configure_radio(nrf)

print("Scout Sending Mode...")


def read_distance_cm():
    trig.value(0)
    utime.sleep_us(2)
    trig.value(1)
    utime.sleep_us(10)
    trig.value(0)

    duration = time_pulse_us(echo, 1, 30000)
    if duration < 0:
        return None
    return duration / 58.0

while True:
    distance_cm = read_distance_cm()

    if distance_cm is None:
        print("No reading")
    elif nrf is None:
        print("Distance: {:.1f} cm (radio offline)".format(distance_cm))
    else:
        # ASCII payload keeps cross-language decoding simple.
        payload = "{:.1f}".format(distance_cm).encode("ascii")
        nrf.stop_listening()
        try:
            nrf.send(payload)
            led.toggle()
            print("Sent Distance: {:.1f} cm".format(distance_cm))
        except OSError as exc:
            msg = str(exc)
            if msg in ("send failed", "timed out"):
                print("Anchor not responding (packet not acknowledged)")
            else:
                print("Radio hardware error; entering offline mode")
                nrf = None
                next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

    # background retry if radio was unavailable
    if nrf is None and utime.ticks_diff(utime.ticks_ms(), next_retry_ms) >= 0:
        nrf = init_radio(max_attempts=1)
        if nrf is not None:
            configure_radio(nrf)
        next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)
            
    utime.sleep_ms(500)