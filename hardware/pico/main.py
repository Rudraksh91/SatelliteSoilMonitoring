"""
╔═══════════════════════════════════════════════════════════╗
║  TERRA-CORE AgriSat — Raspberry Pi Pico Sensor Hub v1.0  ║
║  Dediapada Farm · 21.6277°N, 73.5903°E · Gujarat         ║
╚═══════════════════════════════════════════════════════════╝

SENSORS SUPPORTED:
  • 3× Capacitive Soil Moisture Sensors (Analog)
  • DHT22 Temperature + Humidity
  • DS18B20 Soil Temperature (optional, uncomment below)
  • BH1750 Light Sensor I2C (optional, uncomment below)

WIRING (see wiring_guide.txt for full diagram):
  GPIO26 (ADC0) ← Moisture Sensor 1 AOUT  (Zone 1 / FIELD-A)
  GPIO27 (ADC1) ← Moisture Sensor 2 AOUT  (Zone 2 / FIELD-B)
  GPIO28 (ADC2) ← Moisture Sensor 3 AOUT  (Zone 3 / FIELD-C)
  GPIO22        ← DHT22 Data Pin
  GPIO0  (TX0)  → ESP32 GPIO16 (RX2)
  GPIO1  (RX0)  ← ESP32 GPIO17 (TX2)
  3V3           → Sensor VCC (all sensors)
  GND           → Sensor GND (all sensors)

DEPLOYMENT:
  1. Install MicroPython on Pico (uf2 from micropython.org)
  2. Copy this file as 'main.py' using Thonny IDE or mpremote
  3. Pico will auto-run on power-up
  4. Run calibrate.py first to get your CAL_DRY / CAL_WET values

UART MESSAGE FORMAT (to ESP32, every 2 seconds):
  M1:45.2,M2:38.7,M3:52.1,T:28.4,H:65.3,ST:24.1,L:1200\\n
"""

from machine import ADC, UART, Pin, I2C
import time
import dht

# ─── CALIBRATION VALUES ─────────────────────────────────────────────────────
# Run calibrate.py to find your sensor's actual values.
# Place sensor in DRY AIR → note 16-bit ADC value → set as CAL_DRY
# Place sensor in WET SOIL → note value → set as CAL_WET
#
# Common capacitive v1.2 sensor values at 3.3V:
CAL_DRY = [52000, 52000, 52000]   # Per-zone dry calibration
CAL_WET = [23000, 23000, 23000]   # Per-zone wet calibration

# ─── SENSOR SMOOTHING ────────────────────────────────────────────────────────
SAMPLES = 8          # ADC samples to average per reading
INTERVAL = 2.0       # Seconds between transmissions

# ─── HARDWARE INIT ───────────────────────────────────────────────────────────
adc = [
    ADC(Pin(26)),    # Zone 1 / FIELD-A
    ADC(Pin(27)),    # Zone 2 / FIELD-B
    ADC(Pin(28)),    # Zone 3 / FIELD-C
]

# DHT22 sensor
dht_pin = Pin(22)
dht_sensor = dht.DHT22(dht_pin)

# UART0 → ESP32
uart = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1), timeout=100)

# Onboard LED (status)
# Pico:   led = Pin(25, Pin.OUT)
# Pico W: led = Pin("LED", Pin.OUT)
try:
    led = Pin("LED", Pin.OUT)   # Pico W
except:
    led = Pin(25, Pin.OUT)      # Pico (original)

# ─── OPTIONAL: DS18B20 Soil Temperature ──────────────────────────────────────
# Uncomment if you have DS18B20 connected to GPIO18
# from machine import Pin
# import onewire, ds18x20
# ds_pin   = Pin(18)
# ds_bus   = onewire.OneWire(ds_pin)
# ds       = ds18x20.DS18X20(ds_bus)
# ds_roms  = ds.scan()
# SOIL_TEMP_ENABLED = len(ds_roms) > 0

# ─── OPTIONAL: BH1750 Light Sensor (I2C) ─────────────────────────────────────
# Uncomment if BH1750 connected to SDA=GPIO4, SCL=GPIO5
# i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)
# BH1750_ADDR = 0x23
# LIGHT_ENABLED = True

# ─── FUNCTIONS ───────────────────────────────────────────────────────────────

def read_moisture(zone_idx):
    """
    Read capacitive moisture sensor with multi-sample averaging.
    Returns float 0.0–100.0 (0=bone dry, 100=fully saturated).
    """
    total = 0
    for _ in range(SAMPLES):
        total += adc[zone_idx].read_u16()
        time.sleep_ms(5)
    avg = total / SAMPLES

    dry = CAL_DRY[zone_idx]
    wet = CAL_WET[zone_idx]
    pct = (dry - avg) / (dry - wet) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def read_dht22():
    """
    Read DHT22 temperature and humidity.
    Returns (temp_celsius, humidity_percent) or (None, None) on error.
    """
    try:
        dht_sensor.measure()
        time.sleep_ms(150)
        t = dht_sensor.temperature()
        h = dht_sensor.humidity()
        # Sanity check
        if -40 <= t <= 80 and 0 <= h <= 100:
            return t, h
    except Exception as e:
        pass
    return None, None


def read_soil_temp():
    """Read DS18B20 soil temperature (°C). Returns 0 if not fitted."""
    # Uncomment if DS18B20 is wired:
    # try:
    #     if SOIL_TEMP_ENABLED:
    #         ds.convert_temp()
    #         time.sleep_ms(750)
    #         return round(ds.read_temp(ds_roms[0]), 1)
    # except:
    #     pass
    return 0.0


def read_light():
    """Read BH1750 light level (lux). Returns 0 if not fitted."""
    # Uncomment if BH1750 is wired:
    # try:
    #     if LIGHT_ENABLED:
    #         i2c.writeto(BH1750_ADDR, bytes([0x10]))  # Continuous 1lx
    #         time.sleep_ms(180)
    #         raw = i2c.readfrom(BH1750_ADDR, 2)
    #         return round((raw[0] << 8 | raw[1]) / 1.2, 0)
    # except:
    #     pass
    return 0.0


def build_message(m1, m2, m3, temp, hum, soil_t, lux):
    """
    Build the UART message string.
    All None values are replaced with 0 for robustness.
    """
    t  = round(temp   or 0, 1)
    h  = round(hum    or 0, 1)
    st = round(soil_t or 0, 1)
    lx = int(lux or 0)
    return "M1:{:.1f},M2:{:.1f},M3:{:.1f},T:{:.1f},H:{:.1f},ST:{:.1f},L:{}\n".format(
        m1, m2, m3, t, h, st, lx
    )


def status_blink(n=1, on_ms=80, off_ms=80):
    """Blink onboard LED n times."""
    for _ in range(n):
        led.on()
        time.sleep_ms(on_ms)
        led.off()
        time.sleep_ms(off_ms)


# ─── MAIN LOOP ───────────────────────────────────────────────────────────────
print("=" * 52)
print("TERRA-CORE AgriSat — Pico Sensor Hub v1.0")
print("UART0 TX=GP0  RX=GP1  @9600 baud → ESP32")
print("Sensors: 3× Moisture + DHT22")
print("Calibration (Zone 1-3):")
for i in range(3):
    print("  Zone {}: DRY={} WET={}".format(i+1, CAL_DRY[i], CAL_WET[i]))
print("=" * 52)

# Startup blink
status_blink(3, 200, 100)

error_streak  = 0
tx_count      = 0

while True:
    loop_start = time.ticks_ms()
    led.on()

    try:
        # Read sensors
        m = [read_moisture(i) for i in range(3)]
        temp, hum     = read_dht22()
        soil_t        = read_soil_temp()
        lux           = read_light()

        # Build and transmit
        msg = build_message(m[0], m[1], m[2], temp, hum, soil_t, lux)
        uart.write(msg.encode('ascii'))

        tx_count += 1
        error_streak = max(0, error_streak - 1)

        # Print to USB serial every 5 cycles (for monitoring via Thonny)
        if tx_count % 5 == 0:
            print("[{}] {}".format(tx_count, msg.strip()))
            print("      M: {:.1f}% | {:.1f}% | {:.1f}%  T:{:.1f}°C  H:{:.1f}%".format(
                m[0], m[1], m[2], temp or 0, hum or 0))

        led.off()

    except Exception as e:
        error_streak += 1
        led.off()
        # Send error beacon so ESP32 knows Pico is alive but struggling
        try:
            uart.write("ERR:{}\n".format(error_streak).encode())
        except:
            pass
        print("[ERROR #{}}] {}".format(error_streak, str(e)))

        # Auto-reset after 30 consecutive errors
        if error_streak >= 30:
            print("[RESET] Too many errors — rebooting Pico...")
            time.sleep(2)
            import machine
            machine.reset()

    # Pace the loop to INTERVAL seconds, accounting for sensor read time
    elapsed = time.ticks_diff(time.ticks_ms(), loop_start)
    sleep_ms = max(0, int(INTERVAL * 1000) - elapsed)
    time.sleep_ms(sleep_ms)
