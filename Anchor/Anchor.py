from machine import Pin, SPI
from nrf24l01 import NRF24L01
import utime
import ustruct

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
if nrf is not None:
    # Communication Pipes (Reversed from Scout)
    pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")
    nrf.open_rx_pipe(1, pipes[0])
    nrf.open_tx_pipe(pipes[1])
    nrf.start_listening()
next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

print("Anchor Mirroring Mode...")


def _decode_packet(packet):
    """
    Decode tagged telemetry payload sent by Scout.
    Returns tuple (kind, value) where:
    - ("gyro", (gx, gy, gz))
    - ("distance", distance_cm)
    - (None, None) if unknown

    Supported packet formats:
    - b"G" + 3x int16 little-endian (gyro in centi-deg/s)
    - ASCII float (e.g. b"123.4")
    - 2-byte unsigned int, little-endian, distance in cm
    - 4-byte float32, little-endian, distance in cm
    """
    # nRF fixed payloads are often right-padded with zeros.
    data = packet.rstrip(b"\x00")
    if not data:
        return (None, None)

    if data[0] == 71 and len(data) >= 7:  # ord('G')
        gx_i, gy_i, gz_i = ustruct.unpack("<hhh", data[1:7])
        return ("gyro", (gx_i / 100.0, gy_i / 100.0, gz_i / 100.0))

    # Optional prefix support, e.g. b"D:123.4"
    if data.startswith(b"D:"):
        data = data[2:]
        if not data:
            return (None, None)

    # 1) ASCII number
    try:
        return ("distance", float(data.decode("ascii")))
    except Exception:
        pass

    # 2) uint16 cm
    if len(data) == 2:
        return ("distance", float(ustruct.unpack("<H", data)[0]))

    # 3) float32 cm
    if len(data) == 4:
        return ("distance", float(ustruct.unpack("<f", data)[0]))

    return (None, None)

while True:
    if nrf is not None and nrf.any():
        while nrf.any():
            buf = nrf.recv()
            kind, value = _decode_packet(buf)

            if kind == "gyro":
                gx, gy, gz = value
                print("Received Gyro: gx={:.2f} gy={:.2f} gz={:.2f} deg/s".format(gx, gy, gz))
            elif kind == "distance":
                print("Received Distance: {:.1f} cm".format(value))
            else:
                state = buf[0]  # backward compatibility with old 1-byte packets
                led.value(state)
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