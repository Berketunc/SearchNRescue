from machine import Pin, SPI
from nrf24l01 import NRF24L01
import utime

# 1. Setup LED
led = Pin("LED", Pin.OUT)

# 2. Setup SPI and Radio
spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cfg = {"csn": 14, "ce": 17}
nrf = NRF24L01(spi, Pin(cfg["csn"]), Pin(cfg["ce"]), payload_size=1)

# Communication Pipes (Reversed from Scout)
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
nrf.open_rx_pipe(1, pipes[0])
nrf.open_tx_pipe(pipes[1])
nrf.start_listening()

print("Anchor Mirroring Mode...")

while True:
    if nrf.any():
        while nrf.any():
            buf = nrf.recv()
            state = buf[0] # The byte we sent
            
            led.value(state) # MIMIC the Scout
            print(f"Received Mimic: {state}")
            
    utime.sleep_ms(5)