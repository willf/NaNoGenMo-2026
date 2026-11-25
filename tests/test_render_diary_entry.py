import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from render_diary_entry import load_dataset, render_entry


ROOT = Path(__file__).resolve().parents[1]
DIARY = ROOT / "michigan-trip-diary.csv"
SCRIPT = ROOT / "render_diary_entry.py"
MINIMUM = {
    "date": "2026-05-25", "start_location": "Eagle River, MI",
    "end_location": "Near Lake Linden, MI", "bicycle_miles": 20,
}


class RenderTests(unittest.TestCase):
    def test_first_entry_markdown(self):
        expected = (
            "## 2026-05-25\n\n"
            "I started in Eagle River, MI and covered 20 miles SSW by bicycle. "
            "I ended the day near Lake Linden, MI. The day's county-seat stop was Eagle River (Keweenaw County).\n\n"
            "Daytime reports indicated sunny or clear skies. "
            "The station recorded 0.06 in of precipitation (light rain). "
            "The temperature ranged from 45°F to 85°F. Wind was from WNW. "
            "Wind speed averaged 5.9 mph. The peak gust was 42.6 mph. "
            "Average relative humidity was 59.1%.\n\n"
            "At my starting location (EDT), sunrise was at 6:06 AM and sunset was at 9:35 PM.\n"
        )
        self.assertEqual(render_entry(load_dataset(DIARY), 0), expected)

    def test_offset_uses_row_order(self):
        next_record = dict(MINIMUM, date="2026-04-30")
        self.assertTrue(render_entry([MINIMUM, next_record], 0).startswith("## 2026-05-25\n"))
        self.assertTrue(render_entry([MINIMUM, next_record], 1).startswith("## 2026-04-30\n"))

    def test_rain_layover_stays_put(self):
        entry = render_entry([dict(MINIMUM, bicycle_miles=0, day_status="Rain layover",
                                   start_location="Near Harvey, MI", end_location="Near Harvey, MI",
                                   rain_class="Heavy rain", precip_in="0.55")], 0)
        self.assertIn("I stayed near Harvey, MI for a heavy-rain layover.", entry)
        self.assertIn("0.55 in of precipitation (heavy rain)", entry)
        self.assertNotIn("covered", entry)

    def test_shuttle_is_separate_from_bicycle_miles(self):
        for fields in ({"day_status": "Ride + shuttle"}, {"shuttle_miles": 7.206}):
            with self.subTest(fields=fields):
                row = dict(MINIMUM, **fields, bicycle_miles=40, county_seats_reached="Mackinac, Cheboygan")
                entry = render_entry([row], 0)
                self.assertIn("covered 40 miles by bicycle", entry)
                self.assertIn("I took the Mackinac Bridge shuttle.", entry)
                self.assertIn("county-seat stops were Mackinac, Cheboygan", entry)
                self.assertNotIn("47.2", entry)

    def test_lists_county_seat_places_with_their_counties_in_order(self):
        row = dict(MINIMUM, county_seats_reached="Grand Traverse, Leelanau",
                   county_seat_places_reached="Traverse City, Suttons Bay Township")
        entry = render_entry([row], 0)
        self.assertIn("Traverse City (Grand Traverse County), Suttons Bay Township (Leelanau County)", entry)

    def test_rejects_unpaired_county_seat_places(self):
        for places in ("Traverse City", ""):
            row = dict(MINIMUM, county_seats_reached="Grand Traverse, Leelanau",
                       county_seat_places_reached=places)
            with self.subTest(places=places), self.assertRaisesRegex(ValueError, "one place per county seat"):
                render_entry([row], 0)

    def test_minimum_record_and_additional_fields(self):
        row = dict(MINIMUM, bicycle_miles="1.0", campsite_notes="Reserved", future_data={"food": "bread"})
        entry = render_entry([row], 0)
        self.assertIn("covered 1 mile by bicycle", entry)
        self.assertEqual(entry.count("\n\n"), 1)
        self.assertNotIn("Reserved", entry)

    def test_trace_and_zero_measurements_are_distinct(self):
        dry = dict(MINIMUM, precip_in="0", rain_class="Dry / trace", avg_wind_mph=0, wind_from="Calm")
        trace = dict(dry, precip_in="0.0001", avg_humidity_pct=0)
        self.assertIn("recorded no precipitation", render_entry([dry], 0))
        entry = render_entry([trace], 0)
        self.assertIn("recorded a trace of precipitation", entry)
        self.assertIn("Wind speed averaged 0 mph", entry)
        self.assertIn("Average relative humidity was 0%", entry)

    def test_missing_measurements_can_leave_partial_weather_and_daylight(self):
        row = dict(MINIMUM, daytime_sky="Insufficient reports", low_f=-4.2,
                   gust_mph=None, sunset="20:42", time_zone="CDT")
        entry = render_entry([row], 0)
        self.assertIn("The low was -4.2°F.", entry)
        self.assertIn("At my starting location (CDT), sunset was at 8:42 PM.", entry)
        self.assertNotIn("Daytime reports indicated", entry)
        self.assertNotIn("sunrise", entry)
        self.assertNotIn("gust", entry)

    def test_midnight_and_noon_in_daylight(self):
        entry = render_entry([dict(MINIMUM, sunrise="00:01", sunset="12:03")], 0)
        self.assertIn("sunrise was at 12:01 AM and sunset was at 12:03 PM", entry)

    def test_data_is_encoded_as_markdown_text(self):
        row = dict(MINIMUM, start_location="A *Camp* <Gate> & [Park]\n## Not another date")
        entry = render_entry([row], 0)
        self.assertIn(r"A \*Camp\* &lt;Gate&gt; &amp; \[Park\] ## Not another date", entry)
        self.assertEqual(sum(line.startswith("## ") for line in entry.splitlines()), 1)

    def test_invalid_offsets(self):
        for offset in (-1, 1):
            with self.subTest(offset=offset), self.assertRaisesRegex(IndexError, "valid offsets are 0–0"):
                render_entry([MINIMUM], offset)
        for offset in ("0", 1.0, True):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "integer"):
                render_entry([MINIMUM], offset)

    def test_rejects_missing_required_fields(self):
        for key in MINIMUM:
            row = {k: v for k, v in MINIMUM.items() if k != key}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                render_entry([row], 0)

    def test_rejects_invalid_facts(self):
        cases = [
            ({"date": "2026-02-30"}, "date"),
            ({"date": "20260525"}, "date"),
            ({"bicycle_miles": -1}, "bicycle_miles"),
            ({"bicycle_miles": "  "}, "bicycle_miles"),
            ({"bicycle_miles": "NaN"}, "bicycle_miles"),
            ({"precip_in": "Infinity"}, "precip_in"),
            ({"avg_humidity_pct": 200}, "avg_humidity_pct"),
            ({"low_f": 99, "high_f": 60}, "low_f"),
            ({"sunrise": "25:00"}, "sunrise"),
            ({"sunset": "21:35:17"}, "sunset"),
            ({"end_location": "  "}, "end_location"),
        ]
        for fields, error in cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, error):
                render_entry([dict(MINIMUM, **fields)], 0)


class DatasetTests(unittest.TestCase):
    def test_csv_and_both_json_shapes_render_identically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "diary.CSV"
            record = dict(MINIMUM, high_f=85, avg_humidity_pct=58.4, sunrise="06:06", time_zone="EDT")
            with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=record)
                writer.writeheader()
                writer.writerow(record)
            csv_entry = render_entry(load_dataset(csv_path), 0)
            for payload in ([record], {"metadata": {"source": "collector"}, "rows": [record]}):
                with self.subTest(payload=payload):
                    json_path = root / "diary.json"
                    json_path.write_text(json.dumps(payload), encoding="utf-8")
                    self.assertEqual(render_entry(load_dataset(json_path), 0), csv_entry)

    def test_load_errors(self):
        cases = [
            ("data.json", "[]", "no diary records"),
            ("data.json", '{"rows": [null]}', "list of diary records"),
            ("data.json", "not JSON", "Expecting value"),
            ("data.csv", "date,date\n2026-05-25,2026-05-25\n", "duplicate"),
            ("data.csv", "date\n2026-05-25,extra\n", "more values"),
            ("data.txt", "hello", "must be a .csv or .json"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for name, content, error in cases:
                with self.subTest(name=name, content=content):
                    path = Path(directory) / name
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        load_dataset(path)

    def test_every_real_day_renders(self):
        rows = load_dataset(DIARY)
        self.assertEqual(len(rows), 98)
        entries = [render_entry(rows, offset) for offset in range(len(rows))]
        county_seats = {row["county"]: row["county_seat"] for row in load_dataset(ROOT / "michigan-county-seats.csv")}
        visited = []
        for row, entry in zip(rows, entries):
            with self.subTest(date=row["date"]):
                self.assertTrue(entry.startswith(f'## {row["date"]}\n\n'))
                self.assertIn("precipitation", entry)
                self.assertIn("relative humidity", entry)
                self.assertIn("sunrise was at", entry)
                self.assertIn(f'({row["time_zone"]})', entry)
                self.assertTrue(entry.endswith("\n"))
                counties = row["county_seats_reached"].split(", ") if row["county_seats_reached"] else []
                places = row["county_seat_places_reached"].split(", ") if row["county_seat_places_reached"] else []
                self.assertEqual(places, [county_seats[county] for county in counties])
                visited.extend(counties)
                for county, place in zip(counties, places):
                    self.assertIn(f"{place} ({county} County)", entry)
        self.assertEqual(len(visited), 83)
        self.assertEqual(set(visited), set(county_seats))
        self.assertTrue(entries[-1].startswith("## 2026-08-30\n"))
        self.assertIn("I ended the day in Monroe, MI.", entries[-1])
        self.assertEqual(sum("for a heavy-rain layover" in entry for entry in entries), 7)
        self.assertEqual(sum("Mackinac Bridge shuttle" in entry for entry in entries), 1)


class CliTests(unittest.TestCase):
    def command(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, check=False)

    def test_cli_prints_only_the_selected_entry(self):
        result = self.command(DIARY, 97)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, render_entry(load_dataset(DIARY), 97))
        self.assertEqual(result.stderr, "")

    def test_cli_reports_errors_without_partial_markdown(self):
        for path, offset, expected in [(DIARY, 98, "0–97"), (DIARY, "day", "invalid int"),
                                       (ROOT / "missing-trip-file.csv", 0, "missing-trip-file.csv")]:
            with self.subTest(path=path, offset=offset):
                result = self.command(path, offset)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
