"""
TERRA-CORE AgriSat — Moisture Sensor Calibration Utility
Run BEFORE main.py to find CAL_DRY and CAL_WET for your sensors.

STEPS:
  1. Flash this file to Pico as 'calibrate.py'
  2. Run it: exec(open('calibrate.py').read())
  3. STEP A — Hold sensors in DRY AIR for 30 seconds, note the values
  4. STEP B — Insert sensors in saturated/wet soil, note the values
  5. Copy those values into main.py  CAL_DRY / CAL_WET arrays
"""
from machine import ADC, Pin
import time

adc = [ADC(Pin(26)), ADC(Pin(27)), ADC(Pin(28))]
SAMPLES = 20

def read_avg(sensor):
    t = 0
    for _ in range(SAMPLES):
        t += sensor.read_u16()
        time.sleep_ms(10)
    return t // SAMPLES

print("=" * 55)
print("MOISTURE SENSOR CALIBRATION")
print("Pins: Zone1=GP26  Zone2=GP27  Zone3=GP28")
print("=" * 55)
print("Readings updated every second. Press Ctrl+C to stop.\n")
print("{:<10} {:>12} {:>12} {:>12}".format("Reading", "Zone1(GP26)", "Zone2(GP27)", "Zone3(GP28)"))
print("-" * 50)

n = 0
while True:
    r = [read_avg(adc[i]) for i in range(3)]
    hint = "← DRY values ~52000 | WET values ~23000"
    print("{:<10} {:>12,} {:>12,} {:>12,}   {}".format(
        n, r[0], r[1], r[2], hint if n == 0 else ""))
    n += 1
    time.sleep(1)
