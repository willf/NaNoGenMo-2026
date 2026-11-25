from dataclasses import FrozenInstanceError
from datetime import date, time
from decimal import Decimal
from pathlib import Path
import unittest

from diary_data import CountySeat, DiaryDay, day_at, load_dataset
from render_diary_entry import render_day, render_entry
from render_trip_diary import render_trip


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "michigan-trip-diary.csv"


class DiaryDayTests(unittest.TestCase):
    def test_first_day_exposes_typed_ride_weather_and_daylight_facts(self):
        day = day_at(load_dataset(DATASET), 0)
        self.assertIsInstance(day, DiaryDay)
        self.assertEqual(day.date, date(2026, 5, 25))
        self.assertEqual(day.start.name, "Eagle River, MI")
        self.assertEqual(day.start.latitude, Decimal("47.413164"))
        self.assertEqual(day.end.name, "Near Lake Linden, MI")
        self.assertEqual(day.bicycle_miles, Decimal("20.0"))
        self.assertEqual(day.county_seats, (CountySeat("Keweenaw", "Eagle River"),))
        self.assertEqual(day.weather.precip_in, Decimal("0.06"))
        self.assertEqual(day.weather.avg_humidity_pct, Decimal("59.1"))
        self.assertEqual(day.weather.station_id, "CMX")
        self.assertEqual(day.weather.sky_report_hours, 9)
        self.assertEqual(day.daylight.sunrise, time(6, 6))
        self.assertEqual(day.daylight.time_zone_id, "America/Detroit")

    def test_csv_strings_and_json_numbers_produce_equal_objects(self):
        row = {
            "date": "2026-05-25", "start_location": "Eagle River, MI", "end_location": "Near Lake Linden, MI",
            "bicycle_miles": "20.0", "precip_in": "0", "gust_mph": "", "daytime_sky_hours": "9",
        }
        csv_day = DiaryDay.from_row(row)
        json_day = DiaryDay.from_row(dict(row, bicycle_miles=20.0, precip_in=0, gust_mph=None, daytime_sky_hours=9))
        self.assertEqual(csv_day, json_day)
        self.assertEqual(csv_day.weather.precip_in, Decimal(0))
        self.assertIsNone(csv_day.weather.gust_mph)
        self.assertIsNone(csv_day.daylight.sunset)

    def test_county_seats_are_paired_in_order(self):
        rows = load_dataset(DATASET)
        index = next(i for i, row in enumerate(rows) if row["county_seats_reached"] == "Grand Traverse, Leelanau")
        day = day_at(rows, index)
        self.assertEqual(day.county_seats, (CountySeat("Grand Traverse", "Traverse City"),
                                           CountySeat("Leelanau", "Suttons Bay Township")))

    def test_day_and_its_components_are_immutable(self):
        day = day_at(load_dataset(DATASET), 0)
        with self.assertRaises(FrozenInstanceError):
            day.status = "Rain layover"
        with self.assertRaises(FrozenInstanceError):
            day.weather.sky = "Overcast"

    def test_renderers_accept_typed_days_and_original_records(self):
        rows = load_dataset(DATASET)
        days = [day_at(rows, index) for index in range(len(rows))]
        self.assertIs(day_at(days, 0), days[0])
        self.assertEqual(render_entry(rows, 0), render_day(days, 0))
        self.assertEqual(render_entry(days, 0), render_entry(rows, 0))
        self.assertEqual(render_trip(days), render_trip(rows))

    def test_selecting_one_day_validates_only_that_record(self):
        rows = load_dataset(DATASET)
        dataset = [{"date": "bad date"}, rows[0]]
        self.assertEqual(day_at(dataset, 1), day_at(rows, 0))


if __name__ == "__main__":
    unittest.main()
