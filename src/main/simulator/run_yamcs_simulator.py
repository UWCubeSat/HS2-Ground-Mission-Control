#!/usr/bin/env python3
"""
run_yamcs_simulator.py
git push origin skyfield
"""

import subprocess
import time
from pathlib import Path

from main.python.skyfield_valid import SkyfieldManager

skyfield_manager = SkyfieldManager()
calsphere_1 = skyfield_manager.satellites[0]


def get_project_root(target_name="HS2-Ground-Mission-Control"):
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if parent.name == target_name:
            return parent
    raise FileNotFoundError(f"Could not find project root '{target_name}' in parent paths.")

PROJECT_DIR = get_project_root() # "../../../" need to be ./HS2-Ground-Mission-Control

def main():
    yamcs_proc = subprocess.Popen(["./mvnw", "clean", "compile", "yamcs:run"], cwd=PROJECT_DIR)
    mqtt_proc = subprocess.Popen(["python", "mqtt_skyfield_simulator.py"])
    t0 = skyfield_manager.ts.now()
    delta = 0.5
    while True:
        currently_running = 'mqtt_proc' in locals() and mqtt_proc is not None and mqtt_proc.poll() is None
        print(t0.utc_strftime('%H:%M:%S'), end=" ")
        if skyfield_manager.up_now_fast(calsphere_1, t0):
            if currently_running:
                print("MQTT simulator is already running. Skipping startup.")
            else:
                print("Starting MQTT simulator...")
                # Use Popen so it runs in the background
                mqtt_proc = subprocess.Popen(["python", "mqtt_simulator.py"])
                delta = 5
        else:
            if currently_running:
                print("STOPPING MQTT simulator...")
                # os.killpg(os.getpgid(mqtt_proc.pid), signal.SIGINT)
                mqtt_proc.terminate()
                mqtt_proc.wait()
                delta = 0.5
            else:
                print("MQTT simulator is already shut down. Nothing to stop.")
        t0 += 5/1440
        time.sleep(delta)

    mqtt_proc.terminate()
    yamcs_proc.terminate()

if __name__ == "__main__":
    main()
