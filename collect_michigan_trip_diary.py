#!/usr/bin/env python3
"""Generate a fictional bicycle itinerary from observed daily station weather.

Run with Python 3.10+ and an internet connection. The default route is the
bicycle GeoJSON in this folder. Use --help for date, distance and rain settings.
"""

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from io import BytesIO, StringIO
import json
import math
from pathlib import Path
import re
from statistics import mean
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
IEM = "https://mesonet.agron.iastate.edu"
WEATHER_SOURCE = f"{IEM}/request/daily.phtml"
SKY_SOURCE = f"{IEM}/request/download.phtml"
SOLAR_SOURCE = "https://gml.noaa.gov/grad/solcalc/calcdetails.html"
TIME_ZONE_SOURCE = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Time_Zones/FeatureServer/0"
)
PLACE_SOURCE = "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"
GAZETTEER = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/"
    "2026_Gaz_place_national.zip"
)
NETWORKS = ("MI_ASOS", "WI_ASOS")
WEATHER_FIELDS = (
    "precip_in", "max_temp_f", "min_temp_f", "avg_wind_speed_kts", "max_wind_gust_kts",
    "avg_wind_drct", "avg_rh",
)
SKY_FIELDS = ("skyc1", "skyc2", "skyc3", "skyc4")
# Midpoints of the observed METAR coverage categories, in eighths of sky.
SKY_EIGHTHS = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0, "FEW": 1.5,
               "SCT": 3.5, "BKN": 6, "OVC": 8, "VV": 8, "OVX": 8}
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
# The cycling graph was assembled from separate bicycle and shuttle tracks.
# These are the two shared vertices on its Mackinac–Cheboygan edge.
SHUTTLE_START = (-84.723242, 45.848444)
SHUTTLE_END = (-84.732293, 45.784329)
METERS_PER_MILE = 1609.344
KNOTS_TO_MPH = 1.150779448


def download(url, cache):
    key = hashlib.sha256(url.encode()).hexdigest()
    target = cache / key
    if target.exists():
        return target.read_bytes()
    request = Request(url, headers={"User-Agent": "Michigan bicycle diary/1.0"})
    for attempt in range(3):
        # IEM applies a one-request-per-second throttle to its METAR service.
        if url.startswith(IEM + "/"):
            time.sleep(1.1)
        try:
            with urlopen(request, timeout=75) as response:
                content = response.read()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    target.write_bytes(content)
    return content


def distance(a, b):
    """Great-circle distance in miles between longitude/latitude pairs."""
    lon_a, lat_a = map(math.radians, a)
    lon_b, lat_b = map(math.radians, b)
    h = math.sin((lat_b - lat_a) / 2) ** 2
    h += math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return 3958.76 * 2 * math.asin(min(1, math.sqrt(h)))


def valid_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def compass(degrees):
    return COMPASS[math.floor((degrees % 360 + 11.25) / 22.5) % len(COMPASS)]


def travel_direction(start, end):
    """Compass direction of the net displacement from departure to overnight."""
    if start == end:
        return "Stayed put"
    lon, lat = map(math.radians, start)
    end_lon, end_lat = map(math.radians, end)
    delta_lon = end_lon - lon
    east = math.sin(delta_lon) * math.cos(end_lat)
    north = math.cos(lat) * math.sin(end_lat) - math.sin(lat) * math.cos(end_lat) * math.cos(delta_lon)
    return compass(math.degrees(math.atan2(east, north)))


def point_in_ring(point, ring):
    """Return whether a longitude/latitude point is inside a GeoJSON ring."""
    lon, lat = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (x2 - x1) * (lat - y1) / (y2 - y1):
            inside = not inside
    return inside


def get_time_zones(cache):
    query = {
        "where": "1=1", "geometry": "-91,41,-82,48", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "outSR": 4326, "outFields": "zone", "maxAllowableOffset": 0.0002,
        "geometryPrecision": 6, "f": "geojson",
    }
    url = f"{TIME_ZONE_SOURCE}/query?{urlencode(query)}"
    data = json.loads(download(url, cache))
    names = {"Eastern": "America/Detroit", "Central": "America/Chicago"}
    zones = {}
    for feature in data.get("features", []):
        name = feature["properties"]["zone"]
        if name not in names:
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = [polygons]
        zones[ZoneInfo(names[name])] = polygons
    if len(zones) != len(names):
        raise ValueError("The DOT time-zone response did not contain both route zones.")
    return zones


def solar_coordinates(utc):
    """Return solar declination (radians) and equation of time (minutes)."""
    julian_day = utc.timestamp() / 86400 + 2440587.5
    century = (julian_day - 2451545) / 36525
    longitude = (280.46646 + century * (36000.76983 + 0.0003032 * century)) % 360
    mean_longitude = math.radians(longitude)
    anomaly = math.radians(357.52911 + century * (35999.05029 - 0.0001537 * century))
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    center = math.sin(anomaly) * (1.914602 - century * (0.004817 + 0.000014 * century))
    center += math.sin(2 * anomaly) * (0.019993 - 0.000101 * century) + math.sin(3 * anomaly) * 0.000289
    omega = math.radians(125.04 - 1934.136 * century)
    apparent_longitude = math.radians(longitude + center - 0.00569 - 0.00478 * math.sin(omega))
    obliquity_seconds = 21.448 - century * (46.815 + century * (0.00059 - 0.001813 * century))
    obliquity = math.radians(23 + (26 + obliquity_seconds / 60) / 60 + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(obliquity) * math.sin(apparent_longitude))
    y = math.tan(obliquity / 2) ** 2
    equation = (y * math.sin(2 * mean_longitude) - 2 * eccentricity * math.sin(anomaly)
                + 4 * eccentricity * y * math.sin(anomaly) * math.cos(2 * mean_longitude)
                - 0.5 * y ** 2 * math.sin(4 * mean_longitude)
                - 1.25 * eccentricity ** 2 * math.sin(2 * anomaly))
    return declination, 4 * math.degrees(equation)


def sunrise_sunset(day, point, zone):
    """Calculate minute-rounded local rise and set with NOAA's standard horizon."""
    lon, lat = point
    latitude = math.radians(lat)
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    times = []
    for direction in (1, -1):
        minutes = 720 - 4 * lon
        for _ in range(3):
            declination, equation = solar_coordinates(midnight + timedelta(minutes=minutes))
            cosine = (math.cos(math.radians(90.833)) - math.sin(latitude) * math.sin(declination))
            cosine /= math.cos(latitude) * math.cos(declination)
            hour_angle = math.degrees(math.acos(cosine))
            minutes = 720 - 4 * (lon + direction * hour_angle) - equation
        local = (midnight + timedelta(minutes=minutes, seconds=30)).astimezone(zone)
        times.append(local.replace(second=0, microsecond=0))
    return times


def add_daylight(rows, cache):
    zones = get_time_zones(cache)
    for row in rows:
        point = (row["start_lon"], row["start_lat"])
        zone = next((zone for zone, polygons in zones.items()
                     if any(point_in_ring(point, outer) and not any(point_in_ring(point, h) for h in holes)
                            for outer, *holes in polygons)), None)
        if zone is None:
            raise ValueError(f'No DOT time zone matched {row["date"]} at {point}.')
        rise, sunset = sunrise_sunset(date.fromisoformat(row["date"]), point, zone)
        row.update(sunrise=rise.strftime("%H:%M"), sunset=sunset.strftime("%H:%M"),
                   time_zone=rise.tzname(), time_zone_id=zone.key)


def get_weather(network, start, end, cache):
    metadata_url = f"{IEM}/geojson/network/{network}.geojson"
    metadata = json.loads(download(metadata_url, cache))
    stations = {f["id"]: f for f in metadata["features"]}
    query = [
        ("network", network), ("stations", "_ALL"),
        ("sts", start.isoformat()), ("ets", end.isoformat()),
        *(("var", field) for field in WEATHER_FIELDS),
    ]
    url = f"{IEM}/cgi-bin/request/daily.py?{urlencode(query)}"
    text = download(url, cache).decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    required = {"station", "day", *WEATHER_FIELDS}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError(f"Unexpected IEM CSV header for {network}: {reader.fieldnames}")
    readings = defaultdict(list)
    supplemental = defaultdict(list)
    for row in reader:
        station = stations.get(row["station"])
        if not station or not station["geometry"]:
            continue
        rain, high, low, avg_wind, gust, wind_bearing, humidity = [valid_number(row[field]) for field in WEATHER_FIELDS]
        observed = date.fromisoformat(row["day"])
        observation = {
            "station": row["station"],
            "station_name": station["properties"]["sname"],
            "network": network,
            "coordinates": tuple(station["geometry"]["coordinates"][:2]),
            "precip_in": rain,
            "high_f": high,
            "low_f": low,
            "avg_wind_mph": round(avg_wind * KNOTS_TO_MPH, 1) if avg_wind is not None else None,
            "gust_mph": round(gust * KNOTS_TO_MPH, 1) if gust is not None else None,
            "wind_direction_deg": wind_bearing if wind_bearing is not None and 0 <= wind_bearing <= 360 else None,
            "avg_humidity_pct": round(humidity, 1) if humidity is not None and 0 <= humidity <= 100 else None,
        }
        if rain is not None and high is not None and low is not None and rain >= 0 and low <= high:
            readings[observed].append(observation)
        if observation["wind_direction_deg"] is not None or observation["avg_humidity_pct"] is not None:
            supplemental[observed].append(observation)
    if not readings:
        raise ValueError(f"No complete IEM observations returned for {network}.")
    return readings, supplemental, stations


def sky_reports(needed, catalog, cache):
    """Download and group daytime airport sky reports by station, date, and hour."""
    networks = defaultdict(set)
    for network, station, day in needed:
        networks[network].add((station, day))
    hours = defaultdict(dict)
    for network, station_days in sorted(networks.items()):
        stations = {station: ZoneInfo(catalog[network, station]["properties"]["tzname"])
                    for station, _ in station_days}
        days = [date.fromisoformat(day) for _, day in station_days]
        query = {
            "network": network, "station": ",".join(sorted(stations)), "data": ",".join(SKY_FIELDS),
            "sts": f"{min(days)}T00:00:00Z", "ets": f"{max(days) + timedelta(days=2)}T00:00:00Z",
            "tz": "UTC", "format": "onlycomma", "report_type": 3,
        }
        url = f"{IEM}/cgi-bin/request/asos.py?{urlencode(query)}"
        text = download(url, cache).decode("utf-8-sig")
        reader = csv.DictReader(line for line in StringIO(text) if not line.startswith("#"))
        if not {"station", "valid", *SKY_FIELDS}.issubset(reader.fieldnames or []):
            raise ValueError(f"Unexpected METAR CSV header for {network}: {reader.fieldnames}")
        for report in reader:
            station = report["station"]
            if station not in stations:
                continue
            utc = datetime.fromisoformat(report["valid"]).replace(tzinfo=timezone.utc)
            local = utc.astimezone(stations[station])
            key = (network, station, local.date().isoformat())
            if key not in needed or not 9 <= local.hour < 18:
                continue
            codes = [report[f].strip().upper() for f in SKY_FIELDS]
            amounts = [SKY_EIGHTHS[c] for c in codes if c in SKY_EIGHTHS]
            if not amounts:
                continue
            # Keep one report per clock hour so repeated reports do not skew coverage.
            existing = hours[key].get(local.hour)
            if not existing or utc > existing[0]:
                hours[key][local.hour] = (utc, max(amounts), any(c in {"VV", "OVX"} for c in codes))
    return hours


def add_sky_conditions(rows, catalog, cache):
    """Use the primary station first, then nearby active stations with sky reports."""
    primary = [(r["station_network"], r["station_id"], r["date"]) for r in rows]
    hours = sky_reports(set(primary), catalog, cache)
    backup = {}
    for row, key in zip(rows, primary):
        if len(hours[key]) >= 3:
            continue
        point = (row["start_lon"], row["start_lat"])
        nearby = sorted(
            (distance(point, s["geometry"]["coordinates"]), network, station)
            for (network, station), s in catalog.items()
            if s["geometry"] and s["properties"].get("tzname") and (network, station) != key[:2]
            and (s["properties"].get("archive_begin") or "") <= row["date"]
            and (s["properties"].get("archive_end") or "9999") >= row["date"]
        )[:10]
        backup[key] = [(network, station, row["date"]) for _, network, station in nearby]
    missing = {key for candidates in backup.values() for key in candidates if key not in primary}
    hours.update(sky_reports(missing, catalog, cache))

    for row, key in zip(rows, primary):
        selected = next((source for source in [key, *backup.get(key, [])] if len(hours[source]) >= 3), None)
        values = list(hours[selected or key].values())
        count = len(values)
        cloud = mean(value[1] for value in values) if count >= 3 else None
        if cloud is None:
            label = "Insufficient reports"
        elif sum(value[2] for value in values) * 2 >= count:
            label = "Sky obscured"
        elif cloud < 1:
            label = "Sunny / clear"
        elif cloud < 3:
            label = "Mostly sunny"
        elif cloud < 5:
            label = "Partly cloudy"
        elif cloud < 7:
            label = "Mostly cloudy"
        else:
            label = "Overcast"
        row.update(
            daytime_sky=label,
            daytime_cloud_cover_pct=round(cloud * 12.5, 1) if cloud is not None else None,
            daytime_sky_hours=count,
            sky_station_id=selected[1] if selected else None,
            sky_station_network=selected[0] if selected else None,
        )


def get_places(cache):
    with ZipFile(BytesIO(download(GAZETTEER, cache))) as archive:
        filename = next(name for name in archive.namelist() if name.endswith(".txt"))
        reader = csv.DictReader(StringIO(archive.read(filename).decode("utf-8-sig")), delimiter="|")
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        places = []
        for row in reader:
            if row["USPS"] not in {"MI", "WI", "IN", "OH"}:
                continue
            name = re.sub(r" (city|village|town|CDP)$", "", row["NAME"].strip())
            places.append((f'{name}, {row["USPS"]}', (float(row["INTPTLONG"]), float(row["INTPTLAT"]))))
    return places


@dataclass
class Segment:
    start_mile: float
    end_mile: float
    start: tuple
    end: tuple


class Route:
    def __init__(self, filename):
        graph = json.loads(filename.read_text(encoding="utf-8"))
        features = graph["features"]
        self.seats = sorted(
            (f for f in features if f["geometry"]["type"] == "Point"),
            key=lambda f: f["properties"]["sequence"],
        )
        edges = sorted(
            (f for f in features if f["geometry"]["type"] == "LineString"),
            key=lambda f: f["properties"]["sequence"],
        )
        if len(self.seats) != 83 or len(edges) != 82:
            raise ValueError("Expected the Michigan bicycle graph with 83 seats and 82 edges.")
        self.seat_miles = [0.0]
        self.segments = []
        self.shuttle_mile = None
        self.shuttle_road_miles = 0.0
        self.shuttle_source = graph["shuttle_source_url"]
        for index, edge in enumerate(edges, start=1):
            props = edge["properties"]
            if (props["from_stop"], props["to_stop"]) != (index, index + 1):
                raise ValueError(f"Unexpected seat order at edge {index}.")
            ride_miles = props["bicycle_distance_meters"] / METERS_PER_MILE
            coords = [tuple(p) for p in edge["geometry"]["coordinates"]]
            origin = self.seat_miles[-1]
            if props["mode"] == "bicycle_and_shuttle":
                if self.shuttle_mile is not None:
                    raise ValueError("Expected only one shuttle in the bicycle graph.")
                pickup = coords.index(SHUTTLE_START)
                dropoff = coords.index(SHUTTLE_END, pickup + 1)
                before, after = coords[:pickup + 1], coords[dropoff:]
                pre = self.length(before)
                post = self.length(after)
                pre_miles = ride_miles * pre / (pre + post)
                self.shuttle_mile = origin + pre_miles
                self.shuttle_road_miles = props["shuttle_distance_meters"] / METERS_PER_MILE
                self.add_segments(before, origin, pre_miles)
                self.add_segments(after, self.shuttle_mile, ride_miles - pre_miles)
            else:
                self.add_segments(coords, origin, ride_miles)
            self.seat_miles.append(origin + ride_miles)
        if self.shuttle_mile is None:
            raise ValueError("The bicycle graph is missing the Mackinac Bridge shuttle.")
        self.total = self.seat_miles[-1]
        self.segment_ends = [s.end_mile for s in self.segments]

    @staticmethod
    def length(points):
        return sum(distance(a, b) for a, b in zip(points, points[1:]))

    def add_segments(self, points, origin, miles):
        spans = [(a, b, distance(a, b)) for a, b in zip(points, points[1:]) if a != b]
        if not spans or miles <= 0:
            raise ValueError("A bicycle geometry section has no length.")
        scale = miles / sum(span[2] for span in spans)
        position = origin
        for i, (a, b, length) in enumerate(spans):
            end = origin + miles if i == len(spans) - 1 else position + length * scale
            self.segments.append(Segment(position, end, a, b))
            position = end

    def point(self, mile):
        i = bisect_left(self.seat_miles, mile)
        if i < len(self.seat_miles) and abs(self.seat_miles[i] - mile) < 1e-8:
            return tuple(self.seats[i]["geometry"]["coordinates"])
        if abs(mile - self.shuttle_mile) < 1e-8:
            return SHUTTLE_END
        segment = self.segments[min(bisect_right(self.segment_ends, mile), len(self.segments) - 1)]
        fraction = (mile - segment.start_mile) / (segment.end_mile - segment.start_mile)
        return tuple(a + fraction * (b - a) for a, b in zip(segment.start, segment.end))


def simulate(route, weather, supplemental, places, args):
    named_seats = [
        (f'{f["properties"]["postal_city"]}, MI', tuple(f["geometry"]["coordinates"]))
        for f in route.seats
    ]
    places = places + named_seats

    @lru_cache(maxsize=None)
    def location(mile):
        point = route.point(mile)
        if point == SHUTTLE_END:
            return "Mackinaw City, MI", point
        seat_index = bisect_left(route.seat_miles, mile)
        if seat_index < len(route.seats) and abs(route.seat_miles[seat_index] - mile) < 1e-8:
            return named_seats[seat_index][0], point
        place, _ = min(places, key=lambda entry: distance(point, entry[1]))
        return f"Near {place}", point

    records = []
    position = 0.0
    day = args.start
    while position < route.total - 1e-8:
        if day > args.weather_through:
            raise ValueError(f"The trip is unfinished at the end of the weather window ({args.weather_through}).")
        start_label, start_point = location(position)
        readings = weather.get(day)
        if not readings:
            raise ValueError(f"No usable weather observations for {day}.")
        station = min(readings, key=lambda w: (distance(start_point, w["coordinates"]), w["station"]))
        wind_station = station
        humidity_station = station
        if station["wind_direction_deg"] is None and station["avg_wind_mph"] != 0:
            wind_station = min((r for r in supplemental[day] if r["wind_direction_deg"] is not None),
                               key=lambda r: (distance(start_point, r["coordinates"]), r["station"]), default=station)
        if station["avg_humidity_pct"] is None:
            humidity_station = min((r for r in supplemental[day] if r["avg_humidity_pct"] is not None),
                                   key=lambda r: (distance(start_point, r["coordinates"]), r["station"]), default=station)
        precipitation = station["precip_in"]
        if precipitation >= args.heavy_inches:
            rain, cap = "Heavy rain", 0.0
        elif precipitation >= args.light_inches:
            rain, cap = "Light rain", args.light_miles
        else:
            rain, cap = "Dry / trace", args.dry_miles
        end_mile = min(position + cap, route.total)
        end_label, end_point = location(end_mile)
        crossing = position < route.shuttle_mile <= end_mile
        first = 0 if not records else bisect_right(route.seat_miles, position + 1e-8)
        last = bisect_right(route.seat_miles, end_mile + 1e-8)
        seats_reached = [f["properties"] for f in route.seats[first:last]]
        history_params = urlencode({
            "station": station["station"], "network": station["network"], "mode": "daily",
            "year": day.year, "month": day.month, "day": day.day,
        })
        records.append({
            "date": day.isoformat(), "start_location": start_label, "end_location": end_label,
            "day_status": "Rain layover" if not cap else "Ride + shuttle" if crossing else "Ride",
            "rain_class": rain, "precip_in": precipitation,
            "high_f": station["high_f"], "low_f": station["low_f"],
            "avg_wind_mph": station["avg_wind_mph"], "gust_mph": station["gust_mph"],
            "target_miles": cap, "bicycle_miles": round(end_mile - position, 6),
            "cumulative_miles": round(end_mile, 6),
            "county_seats_reached": ", ".join(seat["county"] for seat in seats_reached),
            "county_seat_places_reached": ", ".join(seat["county_seat"] for seat in seats_reached),
            "seats_reached_count": len(seats_reached),
            "shuttle_miles": round(route.shuttle_road_miles, 4) if crossing else 0,
            "start_lat": round(start_point[1], 6), "start_lon": round(start_point[0], 6),
            "end_lat": round(end_point[1], 6), "end_lon": round(end_point[0], 6),
            "station_id": station["station"], "station_name": station["station_name"],
            "station_network": station["network"], "station_distance_mi": round(distance(start_point, station["coordinates"]), 2),
            "station_lat": station["coordinates"][1], "station_lon": station["coordinates"][0],
            "weather_source_url": f"{IEM}/sites/hist.phtml?{history_params}",
            "travel_direction": travel_direction(start_point, end_point),
            "wind_from": "Calm" if wind_station["avg_wind_mph"] == 0 else (
                compass(wind_station["wind_direction_deg"]) if wind_station["wind_direction_deg"] is not None else None
            ),
            "wind_direction_deg": wind_station["wind_direction_deg"],
            "wind_station_id": wind_station["station"], "wind_station_network": wind_station["network"],
            "avg_humidity_pct": humidity_station["avg_humidity_pct"],
            "humidity_station_id": humidity_station["station"], "humidity_station_network": humidity_station["network"],
        })
        position = end_mile
        day += timedelta(days=1)
    if sum(row["seats_reached_count"] for row in records) != len(route.seats):
        raise ValueError("The diary did not account for all 83 county seats.")
    if sum(row["shuttle_miles"] > 0 for row in records) != 1:
        raise ValueError("The diary did not account for exactly one shuttle crossing.")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path, default=HERE / "michigan-county-seat-bike-graph.geojson")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 25))
    parser.add_argument("--weather-through", type=date.fromisoformat, default=date(2026, 11, 20))
    parser.add_argument("--dry-miles", type=float, default=40.0)
    parser.add_argument("--light-miles", type=float, default=20.0)
    parser.add_argument("--light-inches", type=float, default=0.01)
    parser.add_argument("--heavy-inches", type=float, default=0.50)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "michigan-diary-cache")
    parser.add_argument("--output", type=Path, default=Path("michigan-trip-diary.csv"), help="CSV output path")
    parser.add_argument("--json", type=Path, help="Also export records and assumptions as JSON")
    args = parser.parse_args()
    if not (0 < args.light_miles <= args.dry_miles and 0 < args.light_inches < args.heavy_inches):
        parser.error("Distance targets and rain thresholds must be positive and in ascending order.")
    if not 0 <= (args.weather_through - args.start).days <= 364:
        parser.error("The IEM all-station query accepts a weather window of up to one year.")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    route = Route(args.route)
    places = get_places(args.cache_dir)
    weather = defaultdict(list)
    supplemental = defaultdict(list)
    catalog = {}
    for network in NETWORKS:
        readings, extra, stations = get_weather(network, args.start, args.weather_through, args.cache_dir)
        for day, observations in readings.items():
            weather[day].extend(observations)
        for day, observations in extra.items():
            supplemental[day].extend(observations)
        catalog.update({(network, id_): station for id_, station in stations.items()})
    rows = simulate(route, weather, supplemental, places, args)
    add_sky_conditions(rows, catalog, args.cache_dir)
    add_daylight(rows, args.cache_dir)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if args.json:
        metadata = {
            "start": args.start.isoformat(), "weather_through": args.weather_through.isoformat(),
            "dry_miles": args.dry_miles, "light_miles": args.light_miles,
            "light_inches": args.light_inches, "heavy_inches": args.heavy_inches,
            "weather_source_url": WEATHER_SOURCE, "place_source_url": PLACE_SOURCE,
            "sky_source_url": SKY_SOURCE,
            "solar_source_url": SOLAR_SOURCE, "time_zone_source_url": TIME_ZONE_SOURCE,
            "route_filename": args.route.name, "shuttle_source_url": route.shuttle_source,
        }
        args.json.write_text(json.dumps({"metadata": metadata, "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(f'{len(rows)} days, {route.total:,.1f} bicycle miles, arrival {rows[-1]["date"]}.')


if __name__ == "__main__":
    main()
