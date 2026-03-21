from machine import Pin, SPI
from nrf24l01 import NRF24L01
import utime

# 1. Setup LED and Button
led = Pin("LED", Pin.OUT) 
button = Pin(15, Pin.IN, Pin.PULL_UP) # Button between GP15 and GND

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
next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

# Communication Pipes
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")


def configure_radio(radio):
    radio.open_tx_pipe(pipes[0])
    radio.open_rx_pipe(1, pipes[1])


if nrf is not None:
    configure_radio(nrf)

print("Scout Sending Mode...")

last_state = -1

while True:
    # State: 1 if pressed (0V), 0 if released (3.3V)
    current_state = 1 if button.value() == 0 else 0
    
    if current_state != last_state:
        led.value(current_state) # Local Feedback
        
        if nrf is None:
            print(f"State changed (radio offline): {current_state}")
            last_state = current_state
        else:
            # Transmission
            nrf.stop_listening()
            try:
                # We send a 1-byte state
                nrf.send(bytes([current_state]))
                print(f"Sent: {current_state}")
                last_state = current_state
            except OSError as exc:
                msg = str(exc)
                if msg in ("send failed", "timed out"):
                    print("Anchor not responding (packet not acknowledged)")
                    # avoid spamming retries for unchanged button state
                    last_state = current_state
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
            
    utime.sleep_ms(20) # Smooth polling