"""Load trip exports and convert their records to typed diary days."""

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


@dataclass(frozen=True)
class DayLocation:
    name: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None


@dataclass(frozen=True)
class CountySeat:
    county: str
    place: str | None = None


@dataclass(frozen=True)
class DayWeather:
    sky: str | None = None
    rain_class: str | None = None
    precip_in: Decimal | None = None
    low_f: Decimal | None = None
    high_f: Decimal | None = None
    wind_from: str | None = None
    avg_wind_mph: Decimal | None = None
    gust_mph: Decimal | None = None
    avg_humidity_pct: Decimal | None = None
    wind_direction_deg: Decimal | None = None
    cloud_cover_pct: Decimal | None = None
    sky_report_hours: int | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_network: str | None = None
    station_distance_mi: Decimal | None = None
    station_latitude: Decimal | None = None
    station_longitude: Decimal | None = None
    source_url: str | None = None
    wind_station_id: str | None = None
    wind_station_network: str | None = None
    humidity_station_id: str | None = None
    humidity_station_network: str | None = None
    sky_station_id: str | None = None
    sky_station_network: str | None = None


@dataclass(frozen=True)
class Daylight:
    sunrise: time | None = None
    sunset: time | None = None
    time_zone: str | None = None
    time_zone_id: str | None = None


@dataclass(frozen=True)
class DiaryDay:
    date: date
    start: DayLocation
    end: DayLocation
    bicycle_miles: Decimal
    status: str | None = None
    travel_direction: str | None = None
    target_miles: Decimal | None = None
    cumulative_miles: Decimal | None = None
    shuttle_miles: Decimal | None = None
    county_seats: tuple[CountySeat, ...] = ()
    weather: DayWeather = field(default_factory=DayWeather)
    daylight: Daylight = field(default_factory=Daylight)

    @classmethod
    def from_row(cls, row: Mapping) -> "DiaryDay":
        """Normalize a record from a CSV or JSON export."""
        if not isinstance(row, Mapping):
            raise ValueError("Selected diary record must be a mapping of field names to values.")
        required = ("date", "start_location", "end_location", "bicycle_miles")
        missing = [key for key in required if row.get(key) is None or row[key] == ""]
        if missing:
            raise ValueError("Selected diary record is missing required fields: " + ", ".join(missing))

        day = _text(row, "date")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            raise ValueError("date must be a calendar date in YYYY-MM-DD format.") from None
        if parsed.isoformat() != day:
            raise ValueError("date must be a calendar date in YYYY-MM-DD format.")
        start, end = _text(row, "start_location"), _text(row, "end_location")
        if not start or not end:
            raise ValueError("start_location and end_location must name a location.")
        distance = _number(row, "bicycle_miles", nonnegative=True)
        if distance is None:
            raise ValueError("bicycle_miles is required.")

        counties = _names(row, "county_seats_reached")
        places = _names(row, "county_seat_places_reached")
        if "county_seat_places_reached" in row and len(places) != len(counties):
            raise ValueError("county_seat_places_reached must list one place per county seat, in the same order.")
        seats = tuple(CountySeat(county, places[i] if places else None) for i, county in enumerate(counties))

        return cls(
            date=parsed,
            start=DayLocation(start, _number(row, "start_lat"), _number(row, "start_lon")),
            end=DayLocation(end, _number(row, "end_lat"), _number(row, "end_lon")),
            bicycle_miles=distance,
            status=_text(row, "day_status") or None,
            travel_direction=_text(row, "travel_direction") or None,
            target_miles=_number(row, "target_miles", nonnegative=True),
            cumulative_miles=_number(row, "cumulative_miles", nonnegative=True),
            shuttle_miles=_number(row, "shuttle_miles", nonnegative=True),
            county_seats=seats,
            weather=_weather(row),
            daylight=Daylight(_clock(row, "sunrise"), _clock(row, "sunset"),
                              _text(row, "time_zone") or None, _text(row, "time_zone_id") or None),
        )


def load_dataset(path: Path) -> list[dict]:
    """Read records in export order from a CSV or a JSON export."""
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".json"}:
        raise ValueError("Dataset must be a .csv or .json export from the trip diary collector.")
    with path.open(encoding="utf-8-sig", newline="") as file:
        if suffix == ".csv":
            reader = csv.DictReader(file, strict=True)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                raise ValueError("CSV header contains duplicate column names.")
            rows = list(reader)
            if any(None in row for row in rows):
                raise ValueError("A CSV record has more values than the header has columns.")
        else:
            payload = json.load(file)
            rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Dataset must contain a list of diary records.")
    if not rows:
        raise ValueError("Dataset has no diary records.")
    return rows


def day_at(dataset: Sequence[Mapping | DiaryDay], offset: int) -> DiaryDay:
    """Get a typed diary day by zero-based row offset."""
    if type(offset) is not int:
        raise ValueError("Offset must be an integer.")
    if not dataset:
        raise ValueError("Dataset has no diary records.")
    if not 0 <= offset < len(dataset):
        raise IndexError(f"Offset {offset} is outside the dataset (valid offsets are 0–{len(dataset) - 1}).")
    row = dataset[offset]
    return row if isinstance(row, DiaryDay) else DiaryDay.from_row(row)


def _text(row: Mapping, field: str) -> str:
    value = row.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    return " ".join(value.split())


def _names(row: Mapping, field: str) -> list[str]:
    return [name.strip() for name in _text(row, field).split(",") if name.strip()]


def _number(row: Mapping, field: str, *, nonnegative: bool = False) -> Decimal | None:
    value = row.get(field)
    if value is None or isinstance(value, str) and not value.strip():
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{field} must be a number.") from None
    if not result.is_finite() or nonnegative and result < 0:
        requirement = "a finite nonnegative number" if nonnegative else "a finite number"
        raise ValueError(f"{field} must be {requirement}.")
    return result


def _clock(row: Mapping, field: str) -> time | None:
    value = _text(row, field)
    if not value:
        return None
    error = f"{field} must be a local time in HH:MM format."
    try:
        clock = time.fromisoformat(value)
    except ValueError:
        raise ValueError(error) from None
    if clock.isoformat(timespec="minutes") != value:
        raise ValueError(error)
    return clock


def _weather(row: Mapping) -> DayWeather:
    low, high = _number(row, "low_f"), _number(row, "high_f")
    if low is not None and high is not None and low > high:
        raise ValueError("low_f must be less than or equal to high_f.")
    humidity = _number(row, "avg_humidity_pct", nonnegative=True)
    if humidity is not None and humidity > 100:
        raise ValueError("avg_humidity_pct must be between 0 and 100.")
    sky_hours = _number(row, "daytime_sky_hours", nonnegative=True)
    if sky_hours is not None and sky_hours != int(sky_hours):
        raise ValueError("daytime_sky_hours must be a whole number.")

    return DayWeather(
        sky=_text(row, "daytime_sky") or None,
        rain_class=_text(row, "rain_class") or None,
        precip_in=_number(row, "precip_in", nonnegative=True),
        low_f=low,
        high_f=high,
        wind_from=_text(row, "wind_from") or None,
        avg_wind_mph=_number(row, "avg_wind_mph", nonnegative=True),
        gust_mph=_number(row, "gust_mph", nonnegative=True),
        avg_humidity_pct=humidity,
        wind_direction_deg=_number(row, "wind_direction_deg"),
        cloud_cover_pct=_number(row, "daytime_cloud_cover_pct", nonnegative=True),
        sky_report_hours=int(sky_hours) if sky_hours is not None else None,
        station_id=_text(row, "station_id") or None,
        station_name=_text(row, "station_name") or None,
        station_network=_text(row, "station_network") or None,
        station_distance_mi=_number(row, "station_distance_mi", nonnegative=True),
        station_latitude=_number(row, "station_lat"),
        station_longitude=_number(row, "station_lon"),
        source_url=_text(row, "weather_source_url") or None,
        wind_station_id=_text(row, "wind_station_id") or None,
        wind_station_network=_text(row, "wind_station_network") or None,
        humidity_station_id=_text(row, "humidity_station_id") or None,
        humidity_station_network=_text(row, "humidity_station_network") or None,
        sky_station_id=_text(row, "sky_station_id") or None,
        sky_station_network=_text(row, "sky_station_network") or None,
    )
