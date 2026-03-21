from machine import Pin, SPI
from nrf24l01 import NRF24L01
import utime

# 1. Setup LED and Button
led = Pin("LED", Pin.OUT) 
button = Pin(15, Pin.IN, Pin.PULL_UP) # Button between GP15 and GND

# 2. Setup SPI and Radio
spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cfg = {"csn": 14, "ce": 17}
nrf = NRF24L01(spi, Pin(cfg["csn"]), Pin(cfg["ce"]), payload_size=1)

# Communication Pipes
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
nrf.open_tx_pipe(pipes[0])
nrf.open_rx_pipe(1, pipes[1])

print("Scout Sending Mode...")

last_state = -1

while True:
    # State: 1 if pressed (0V), 0 if released (3.3V)
    current_state = 1 if button.value() == 0 else 0
    
    if current_state != last_state:
        led.value(current_state) # Local Feedback
        
        # Transmission
        nrf.stop_listening()
        try:
            # We send a 1-byte state
            result = nrf.send(bytes([current_state]))
            if not result:
                print("Failed to reach Anchor...")
            else:
                print(f"Sent: {current_state}")
            last_state = current_state
        except OSError:
            print("Radio hardware error")
            
    utime.sleep_ms(20) # Smooth polling