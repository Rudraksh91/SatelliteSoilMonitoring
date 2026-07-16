#!/usr/bin/env python3
"""
TERRA-CORE — Manual solenoid valve test (single valve, direct Pi GPIO)

Wiring:
  Relay IN  -> Pi GPIO23 (BCM) / physical pin 16
  Relay VCC -> 5V, Relay GND -> Pi GND
  Relay is ACTIVE-HIGH (confirmed on FILMAX 12V solenoid + this relay board,
  NO terminal wiring verified correct): GPIO HIGH = relay energized = valve OPEN
                                         GPIO LOW  = relay de-energized = valve CLOSED

  NOTE: agrisat-brain.service (LoRa init, via SX127x's default board
  pin preset) holds GPIO4/13/17/18/22/27 claimed even with no physical
  LoRa module attached — confirmed via /sys/kernel/debug/gpio. GPIO23
  is free and doesn't collide with any of that.

Usage:
  python3 test_valve.py open        # open and leave open
  python3 test_valve.py close       # close (safe/default state)
  python3 test_valve.py status      # read current commanded state
  python3 test_valve.py pulse 5     # open for 5s, then auto-close
"""
import sys
import time
import RPi.GPIO as GPIO

PIN = 23
ACTIVE_LOW = False


def setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(PIN, GPIO.OUT, initial=GPIO.HIGH if ACTIVE_LOW else GPIO.LOW)


def set_valve(open_: bool):
    if ACTIVE_LOW:
        GPIO.output(PIN, GPIO.LOW if open_ else GPIO.HIGH)
    else:
        GPIO.output(PIN, GPIO.HIGH if open_ else GPIO.LOW)


def get_state() -> bool:
    is_low = GPIO.input(PIN) == GPIO.LOW
    return is_low if ACTIVE_LOW else (not is_low)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1].lower()
    setup()
    try:
        if cmd == "open":
            set_valve(True)
            print(f"[GPIO{PIN}] Valve OPEN — relay energized")
        elif cmd == "close":
            set_valve(False)
            print(f"[GPIO{PIN}] Valve CLOSED — relay de-energized")
        elif cmd == "status":
            print(f"[GPIO{PIN}] Valve is {'OPEN' if get_state() else 'CLOSED'}")
        elif cmd == "pulse":
            secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5
            print(f"[GPIO{PIN}] Opening for {secs}s, then auto-closing...")
            set_valve(True)
            time.sleep(secs)
            set_valve(False)
            print(f"[GPIO{PIN}] Valve CLOSED (auto)")
        else:
            print(__doc__)
    finally:
        if cmd != "open":
            set_valve(False)


if __name__ == "__main__":
    main()
