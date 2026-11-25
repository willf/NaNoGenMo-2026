#!/usr/bin/env python3
"""Render one day's ride and weather as a factual Markdown diary entry.

Accepts the collector's CSV or JSON export. Offsets are zero-based and follow
the dataset's row order, so offset 0 renders the first day.
"""

import argparse
from collections.abc import Mapping, Sequence
import csv
from datetime import time
from decimal import Decimal
import html
from pathlib import Path
import re

from diary_data import CountySeat, Daylight, DayWeather, DiaryDay, day_at, load_dataset
from utils import display_number, pluralize, display_date_with_weekday_and_month


def markdown_text(value: str) -> str:
    """Insert data as plain text within a Markdown paragraph."""
    escaped = html.escape(value, quote=False)
    return re.sub(r"([\\`*_\[\]~])", r"\\\1", escaped)


def location_phrase(label: str, preposition: str) -> str:
    if label.lower().startswith("near "):
        return "near " + markdown_text(label[5:])
    return preposition + " " + markdown_text(label)


def ride_paragraph(day: DiaryDay) -> str:
    start, end = day.start.name, day.end.name
    distance = day.bicycle_miles
    status = (day.status or "").lower()
    rain = (day.weather.rain_class or "").lower()
    direction = day.travel_direction
    took_shuttle = status == "ride + shuttle" or bool(day.shuttle_miles)
    miles = display_number(distance)
    unit = pluralize(miles, "mile")
    if not distance and start == end and not took_shuttle:
        layover = (
            " for a heavy-rain layover"
            if status == "rain layover" or rain == "heavy rain"
            else ""
        )
        sentences = [
            f"I stayed {location_phrase(start, 'in')}{layover}. My bicycle mileage was 0 miles."
        ]
    else:
        bearing = (
            f" {markdown_text(direction)}"
            if direction and direction.lower() != "stayed put"
            else ""
        )
        sentences = [
            f"I started {location_phrase(start, 'in')} and covered {miles} {unit}{bearing} by bicycle."
        ]
        if took_shuttle:
            sentences.append("I took the Mackinac Bridge shuttle.")
        sentences.append(f"I ended the day {location_phrase(end, 'in')}.")
    if day.county_seats:
        stops = ", ".join(
            f"{markdown_text(seat.place)} ({markdown_text(seat.county)} County)"
            if seat.place
            else markdown_text(seat.county)
            for seat in day.county_seats
        )
        label = pluralize(
            len(day.county_seats), "county-seat stop was", "county-seat stops were"
        )
        sentences.append(f"The day's {label} {stops}.")
    return " ".join(sentences)


def weather_paragraph(weather: DayWeather) -> str:
    sentences = ["Weather for the day was as follows: "]
    sky = (weather.sky or "").lower()
    if sky and sky != "insufficient reports":
        condition = {
            "sunny / clear": "sunny or clear skies",
            "sky obscured": "an obscured sky",
        }.get(sky, sky + " skies")
        sentences.append(f"Daytime reports indicated {markdown_text(condition)}.")

    precipitation = weather.precip_in
    rain = (weather.rain_class or "").lower()
    if precipitation is not None:
        if not precipitation:
            sentences.append("The station recorded no precipitation.")
        elif precipitation == Decimal("0.0001") and rain == "dry / trace":
            sentences.append("The station recorded a trace of precipitation.")
        else:
            classification = (
                f" ({markdown_text(rain)})" if rain and rain != "dry / trace" else ""
            )
            sentences.append(
                f"The station recorded {display_number(precipitation, 4)} in of precipitation{classification}."
            )
    elif rain:
        classification = "dry or trace precipitation" if rain == "dry / trace" else rain
        sentences.append(f"The day was classified as {markdown_text(classification)}.")

    low, high = weather.low_f, weather.high_f
    if low is not None and high is not None:
        sentences.append(
            f"The temperature ranged from {display_number(low)}°F to {display_number(high)}°F."
        )
    elif low is not None:
        sentences.append(f"The low was {display_number(low)}°F.")
    elif high is not None:
        sentences.append(f"The high was {display_number(high)}°F.")

    wind_from = weather.wind_from or ""
    wind_mph = weather.avg_wind_mph
    gust_mph = weather.gust_mph
    if wind_from.lower() == "calm":
        sentences.append("The reported wind direction was calm.")
    elif wind_from:
        sentences.append(f"Wind was from {markdown_text(wind_from)}.")
    if wind_mph is not None:
        sentences.append(f"Wind speed averaged {display_number(wind_mph)} mph.")
    if gust_mph is not None:
        sentences.append(f"The peak gust was {display_number(gust_mph)} mph.")

    humidity = weather.avg_humidity_pct
    if humidity is not None:
        sentences.append(f"Average relative humidity was {display_number(humidity)}%.")
    return " ".join(sentences)


def clock_text(clock: time) -> str:
    suffix = "AM" if clock.hour < 12 else "PM"
    return f"{clock.hour % 12 or 12}:{clock.minute:02d} {suffix}"


def daylight_paragraph(daylight: Daylight) -> str:
    times = [("sunrise", daylight.sunrise), ("sunset", daylight.sunset)]
    events = [
        f"{field} was at {clock_text(clock)}"
        for field, clock in times
        if clock is not None
    ]
    if not events:
        return ""
    zone = daylight.time_zone
    local = f" ({markdown_text(zone)})" if zone else ""
    return f"At my starting location{local}, " + " and ".join(events) + "."


def goal_str(goal: CountySeat) -> str:
    if goal.place:
        return f"{markdown_text(goal.place)} in {markdown_text(goal.county)} County"
    return f"{markdown_text(goal.county)} County"


def goal_paragraph(day, next_day: DiaryDay) -> str:
    goals = day.county_seats
    will_reach = True if goals else False
    if not goals:
        goals = next_day.county_seats
    if not goals:
        return ""
    first_goal = goals[0]
    if not will_reach:
        return f"My goal for the day was to reach {goal_str(first_goal)}."
    if len(goals) > 1:
        goal_strs = [goal_str(goal) for goal in goals]
        return f"My goal for the day was to reach {', '.join(goal_strs[:-1])} and {goal_strs[-1]}."
    return f"My goal for the day was to reach {goal_str(first_goal)}."


def rounded_time(clock: time, offset_minutes: int) -> time:
    minutes = clock.hour * 60 + clock.minute + offset_minutes
    rounded = (minutes + 15) // 30 * 30 % (24 * 60)
    hour, minute = divmod(rounded, 60)
    return time(hour, minute)


def wake_paragraph(day) -> str:
    sunrise = day.daylight.sunrise if day.daylight else None
    if sunrise is not None:
        planned_wake_time = rounded_time(sunrise, -30)
        return f"I set my alarm for at {clock_text(planned_wake_time)}."
    return ""


def sleep_paragraph(day: DiaryDay) -> str:
    sunset = day.daylight.sunset if day.daylight else None
    if sunset is not None:
        planned_sleep_time = rounded_time(sunset, 30)
        return f"I went to bed at {clock_text(planned_sleep_time)}."
    return ""


def direction_to_degrees(direction: str) -> int | None:
    mapping = {
        "N": 0,
        "E": 90,
        "ENE": 67,
        "ESE": 112,
        "NE": 45,
        "NNE": 22,
        "NNW": 337,
        "NW": 315,
        "S": 180,
        "SE": 135,
        "SSE": 157,
        "SSW": 202,
        "SW": 225,
        "W": 270,
        "WNW": 292,
        "WSW": 247,
    }
    return mapping.get(direction)


def compare_wind_directions(
    wind_direction: str | None, bike_direction: str | None
) -> str:
    # convert wind and bike directions to degrees
    if wind_direction is None or bike_direction is None:
        return ""
    wind_deg = direction_to_degrees(wind_direction)
    bike_deg = direction_to_degrees(bike_direction)

    if wind_deg is None or bike_deg is None:
        return ""

    degrees_diff = (wind_deg - bike_deg) % 360
    # heavy tailwinds, tailwind, headwind, heavy headwinds
    if degrees_diff == 0:
        return "I had a tailwind during the ride."
    if degrees_diff == 180:
        return "I faced a headwind during the ride."
    if 0 < degrees_diff < 90:
        return "I had a cross-tailwind during the ride."
    if 90 < degrees_diff < 180:
        return "I had a cross-headwind during the ride."
    if 180 < degrees_diff < 270:
        return "I had a cross-headwind during the ride."
    if 270 < degrees_diff < 360:
        return "I had a cross-tailwind during the ride."
    return ""


def headwinds_paragraph(day: DiaryDay) -> str:
    wind_direction = day.weather.wind_from
    bike_direction = day.travel_direction
    return compare_wind_directions(wind_direction, bike_direction)


def difficulty_paragraph(day: DiaryDay) -> str:
    miles = day.bicycle_miles
    if miles > 50:
        return "Today's ride was very challenging."
    if miles > 30:
        return "Today's ride was moderately challenging."
    return "Today's ride was relatively easy."


def tiredness_paragraph(day: DiaryDay, previous_day: DiaryDay | None) -> str:
    if not previous_day:
        return ""
    miles_yesterday = previous_day.bicycle_miles
    if miles_yesterday > 50:
        return "I was quite tired from yesterday's ride."
    if miles_yesterday > 30:
        return "I felt somewhat tired from yesterday's ride."
    return "I felt fine after yesterday's ride."


def humidy_paragraph(day: DiaryDay) -> str:
    humidity = day.weather.avg_humidity_pct
    if humidity is None:
        return ""
    if humidity > 80:
        return "It was very humid during the ride."
    if humidity > 60:
        return "It was moderately humid during the ride."
    return "It was relatively dry during the ride."


def heat_paragraph(day: DiaryDay) -> str:
    temperature = day.weather.high_f
    if temperature is None:
        return ""
    if temperature > 90:
        return "It was very hot during the ride."
    if temperature > 75:
        return "It was moderately warm during the ride."
    if temperature > 60:
        return "It was pleasantly cool during the ride."
    return "It was quite cold during the ride."


def render_day(
    days: Sequence[Mapping | DiaryDay],
    index: int,
) -> str:
    """Write a Markdown entry using the selected day and its neighbors."""
    day = day_at(days, index)
    next_day = day_at(days, index + 1) if index + 1 < len(days) else day
    previous_day = day_at(days, index - 1) if index > 0 else None
    paragraphs = [
        f"## {display_date_with_weekday_and_month(day.date)}",
        goal_paragraph(day, next_day),
        wake_paragraph(day),
        tiredness_paragraph(day, previous_day),
        ride_paragraph(day),
        headwinds_paragraph(day),
        humidy_paragraph(day),
        heat_paragraph(day),
        difficulty_paragraph(day),
        weather_paragraph(day.weather),
        daylight_paragraph(day.daylight),
        sleep_paragraph(day),
    ]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph) + "\n"


def render_entry(days: Sequence[Mapping | DiaryDay], offset: int) -> str:
    """Render one entry selected by its zero-based offset."""
    return render_day(days, offset)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset", type=Path, help="path to a collector CSV or JSON export"
    )
    parser.add_argument(
        "offset", type=int, help="zero-based diary row offset (0 is the first day)"
    )
    args = parser.parse_args(argv)
    try:
        entry = render_entry(load_dataset(args.dataset), args.offset)
    except (OSError, ValueError, IndexError, csv.Error) as error:
        parser.error(str(error))
    print(entry, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
