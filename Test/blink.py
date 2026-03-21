import machine
import time

# Initialize the onboard LED (GP25)
led = machine.Pin(25, machine.Pin.OUT)

# Loop to blink the LED
while True:
    led.toggle()  # Turns LED on if off, off if on
    time.sleep(0.5)  # Wait for 0.5 seconds