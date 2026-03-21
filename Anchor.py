from machine import Pin, SPI
from nrf24l01 import NRF24L01
import utime

# Optional runtime override injected by dashboard launcher.
USE_GP15_LED = bool(globals().get("USE_GP15_LED", False))

# 1. Setup LED
led = Pin(15 if USE_GP15_LED else "LED", Pin.OUT)

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
                payload_size=1,
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
if nrf is not None:
    # Communication Pipes (Reversed from Scout)
    pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
    nrf.open_rx_pipe(1, pipes[0])
    nrf.open_tx_pipe(pipes[1])
    nrf.start_listening()
next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

print("Anchor Mirroring Mode...")

while True:
    if nrf is not None and nrf.any():
        while nrf.any():
            buf = nrf.recv()
            state = buf[0] # The byte we sent

            led.value(state) # MIMIC the Scout
            print(f"Received Mimic: {state}")

    # background retry if radio was unavailable
    if nrf is None and utime.ticks_diff(utime.ticks_ms(), next_retry_ms) >= 0:
        nrf = init_radio(max_attempts=1)
        if nrf is not None:
            pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
            nrf.open_rx_pipe(1, pipes[0])
            nrf.open_tx_pipe(pipes[1])
            nrf.start_listening()
        next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)
            
    utime.sleep_ms(5)