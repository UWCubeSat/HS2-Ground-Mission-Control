"""
skyfield_valid.py

Methods for valid downlink streaming to YAMCS
"""

import os
import time

from skyfield.api import EarthSatellite, Loader, load, wgs84
from skyfield.timelib import Time, Timescale

# A TLE file was the position of a satellite when it sent it allowing position to be predicted (SGP4) in any time.
TLE_URL = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=900&FORMAT=tle' # redownload to get up-to-date TLE
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(CURRENT_DIR, 'skyfield-data')
DATA_FILE = 'gp.php'

DEFAULT_ELEVATION_THRESHOLD = 10.0
LOCATIONS = [
        ("Absheron, Azerbaijan", wgs84.latlon(40.466389, 49.486111)),
        ("Punta Arenas, Chile", wgs84.latlon(-53.041556, -70.846944)),
        ("Santiago, Chile", wgs84.latlon(-33.364639, -70.772250)),
        ("Blönduós, Iceland", wgs84.latlon(65.647194, -20.245111)),
        ("Kandy, Sri Lanka", wgs84.latlon(7.274278, 80.724833)),
        ("Pretoria, South Africa", wgs84.latlon(-25.860306, 28.453750)),
]

SECONDS_IN_A_DAY = 86400
HOUR = 3600/SECONDS_IN_A_DAY # 1/24
MINUTE = 60/SECONDS_IN_A_DAY # 1/1440
SECOND = 1/SECONDS_IN_A_DAY  # 1/86400

QUERY = 5*MINUTE

# ===HELPER METHODS - NOT USED ==============================================
def find_all_valid_sat(locations: list, satellites: list, total_needed: int = 10) -> list:
    """Returns a list of satellite names that visit all locations within 2 days."""
    ts = load.timescale()
    t0 = ts.now()
    t1 = t0 + 2

    valid_sats = []
    for sat in satellites:
        visited = set()
        for name, site in locations:
            _, events = sat.find_events(site, t0, t1, altitude_degrees=80.0)
            if len(events) > 0:
                visited.add(name)
            else:
                break

        if len(visited) == len(locations):
            valid_sats.append(sat.name)

        if len(valid_sats) >= total_needed:
            break
            
    return valid_sats

def get_sat_location_at_time(satellite: EarthSatellite, ts: Timescale, time: Time = None) -> tuple:
    """
    Returns lattitude and longitude of satellite at time. (subpoint.elevation if needed)
    """
    if time is None:
        time = ts.now()
    geocentric = satellite.at(time)
    # Convert position to sub-satellite latitude, longitude, and elevation
    subpoint = wgs84.subpoint(geocentric)
    print(
        f"Time: {time.utc_strftime('%Y-%m-%d %H:%M:%S')} UTC | "
        f"Lat: {subpoint.latitude.degrees:.2f}° | "
        f"Long: {subpoint.longitude.degrees:.2f}° | "
        f"Alt: {subpoint.elevation.km:.1f} km"
    )

    return subpoint.latitude.degrees, subpoint.longitude.degrees

def up_now(satellite: EarthSatellite, locations: list, ts: Timescale,  t0: Time = None) -> bool:
    """Returns boolean if satellite is near a ground station at given time."""
    if t0 is None:
        t0 = ts.now()
    t_query_start = t0 - 1
    t_query_end = t0 + 1

    for _, site in locations:
        t, events = satellite.find_events(site, t_query_start, t_query_end, altitude_degrees=10.0) # fast enough to recalc every query.
        i=0
        while i < len(events):
            if events[i] == 0:
                rise_time = t[i]
                
                set_time = None
                for j in range(i + 1, len(events)):
                    if events[j] == 2:
                        set_time = t[j]
                        break
                
                if set_time is not None and rise_time.tt <= t0.tt <= set_time.tt:
                    return True
            i += 1
    return False
# ===========================================================================

class SkyfieldManager:
    def __init__(self, TLE_URL = TLE_URL, LOCATIONS = LOCATIONS, DEFAULT_ELEVATION_THRESHOLD = DEFAULT_ELEVATION_THRESHOLD):
        self.ts = load.timescale()
        self.satellites = self.get_satellites(TLE_URL)
        self.locations = LOCATIONS
        self.DEFAULT_ELEVATION_THRESHOLD = DEFAULT_ELEVATION_THRESHOLD
    
    def get_satellites(self, TLE_URL):
        load = Loader(DATA_DIR)
        if os.path.exists(f"{DATA_DIR}/{DATA_FILE}"):
            return load.tle_file(DATA_FILE)
        return load.tle_file(TLE_URL)

    def up_now_fast(self, satellite: EarthSatellite, t0: Time = None) -> bool:
        """Same behavior as up_now() but pure math."""
        if t0 is None:
            t0 = self.ts.now()

        for _, site in self.locations:
            difference = satellite - site
            topocentric = difference.at(t0)
            alt, _, _ = topocentric.altaz()
            if alt.degrees >= self.DEFAULT_ELEVATION_THRESHOLD:
                return True
        return False

def main():
    """Example usage"""
    skyfield_manager = SkyfieldManager()
    calsphere_1 = skyfield_manager.satellites[0]

    t0 = skyfield_manager.ts.now()
    while True:
        print(skyfield_manager.up_now_fast(calsphere_1, t0), t0.utc_strftime('%H:%M:%S'))
        t0 += QUERY
        time.sleep(0.2)

if __name__ == "__main__":
    main()