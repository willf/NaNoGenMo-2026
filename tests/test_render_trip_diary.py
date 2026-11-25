import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from render_diary_entry import load_dataset, render_entry
from render_trip_diary import render_trip


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "michigan-trip-diary.csv"
SCRIPT = ROOT / "render_trip_diary.py"
DAY = {
    "date": "2026-05-25", "start_location": "Eagle River, MI",
    "end_location": "Near Lake Linden, MI", "bicycle_miles": 20,
    "county_seats_reached": "Keweenaw", "county_seat_places_reached": "Eagle River",
}


class RenderTripTests(unittest.TestCase):
    def test_preface_and_postscript_surround_all_entries(self):
        rows = load_dataset(DATASET)
        markdown = render_trip(rows)
        headings = re.findall(r"^## (.+)$", markdown, re.MULTILINE)
        self.assertEqual(headings, ["Preface", *(row["date"] for row in rows), "Postscript"])
        body_start = markdown.index("## 2026-05-25")
        body_end = markdown.index("\n\n## Postscript")
        self.assertEqual(markdown[body_start:body_end] + "\n",
                         "\n".join(render_entry(rows, offset) for offset in range(len(rows))))
        self.assertIn("historical station weather", markdown[:body_start])
        postscript = markdown[body_end:]
        self.assertIn("began in Eagle River, MI on 2026-05-25", postscript)
        self.assertIn("ended in Monroe, MI on 2026-08-30", postscript)
        self.assertIn("98 diary days", postscript)
        self.assertIn("2,904.4 bicycle miles", postscript)
        self.assertIn("83 county seats", postscript)
        self.assertIn("7 heavy-rain layovers", postscript)
        self.assertIn("7.2 vehicle miles", postscript)

    def test_orders_input_and_uses_its_actual_totals(self):
        layover = dict(DAY, date="2026-05-26", start_location="Near Lake Linden, MI",
                       bicycle_miles=0, day_status="Rain layover")
        ride = dict(DAY, date="2026-05-27", start_location="Near Lake Linden, MI",
                    end_location="Houghton, MI", bicycle_miles="3.5", county_seats_reached="Houghton",
                    county_seat_places_reached="Houghton")
        markdown = render_trip([ride, layover, DAY])
        self.assertEqual(re.findall(r"^## (2026-.+)$", markdown, re.MULTILINE),
                         ["2026-05-25", "2026-05-26", "2026-05-27"])
        self.assertIn("Over 3 diary days, the rider logged 23.5 bicycle miles.", markdown)
        self.assertIn("The itinerary reached 2 county seats.", markdown)
        self.assertIn("It included 1 heavy-rain layover.", markdown)
        self.assertIn("ended in Houghton, MI on 2026-05-27", markdown)


class TripCliTests(unittest.TestCase):
    def command(self, directory, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=directory,
                              capture_output=True, text=True, check=False)

    def test_default_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copyfile(DATASET, path / DATASET.name)
            result = self.command(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((path / "michigan-trip-diary.md").read_text(encoding="utf-8"),
                             render_trip(load_dataset(DATASET)))

    def test_alternative_json_dataset_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "trip.json").write_text(json.dumps({"rows": [DAY]}), encoding="utf-8")
            result = self.command(path, "trip.json", "--output", "day.md")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((path / "day.md").read_text(encoding="utf-8"), render_trip([DAY]))

    def test_invalid_row_preserves_existing_diary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "bad.json").write_text(json.dumps([DAY, dict(DAY, date="2026-02-30")]), encoding="utf-8")
            output = path / "michigan-trip-diary.md"
            output.write_text("Earlier draft\n", encoding="utf-8")
            result = self.command(path, "bad.json")
            self.assertEqual(result.returncode, 2)
            self.assertIn("date must be a calendar date", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "Earlier draft\n")

    def test_output_cannot_replace_the_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            dataset = path / "trip.json"
            contents = json.dumps([DAY])
            dataset.write_text(contents, encoding="utf-8")
            result = self.command(path, dataset, "--output", dataset)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be different files", result.stderr)
            self.assertEqual(dataset.read_text(encoding="utf-8"), contents)


if __name__ == "__main__":
    unittest.main()
