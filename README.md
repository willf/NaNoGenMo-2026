# Michigan county-seat bicycle diary

This National Novel Generation Month project simulates a bicycle trip through Michigan’s 83 county seats using historical weather observations. The bundled [dataset](michigan-trip-diary.csv) and [factual diary](michigan-trip-diary.md) cover 98 days, from May 25 through August 30, 2026.

Read the diary and explore the interactive bike route map on the [project website](https://willf.github.io/NaNoGenMo-2026/).

Run the commands below from the project folder with Python 3.10 or newer and system time-zone data. The scripts use the Python standard library. Data collection needs internet access when source files are absent from the cache. Rendering works with local exports.

## Generate the data

```bash
python3 collect_michigan_trip_diary.py
```

The collector follows [the bicycle route graph](michigan-county-seat-bike-graph.geojson) and writes `michigan-trip-diary.csv`. It records each day’s start and overnight locations, mileage, county seats and their towns or cities, station weather, and local sunrise and sunset.

By default the rider starts on May 25, 2026 and rides up to 40 miles on a dry day. A daily precipitation total from 0.01 to under 0.50 inches limits the ride to 20 miles. At 0.50 inches or more, the rider stays put. The collector applies these rules using the reporting station selected for the morning location and runs until the rider reaches Monroe.

This command also writes a JSON export alongside the CSV.

```bash
python3 collect_michigan_trip_diary.py --json michigan-trip-diary.json
```

Use `--output` to write the CSV to a different path. Use `--start` or `--weather-through` to change the weather window, `--dry-miles` or `--light-miles` to change the riding targets, and `--light-inches` or `--heavy-inches` to change the rain thresholds. `--weather-through` sets the last date available to the simulation. Use `--cache-dir` to choose where downloaded source files are stored. Run `python3 collect_michigan_trip_diary.py --help` for the full list of options.

## Render one diary entry

```bash
python3 render_diary_entry.py michigan-trip-diary.csv 0
```

The renderer accepts the CSV or JSON export and a zero-based row offset. Offset `0` selects May 25 in the bundled dataset. The output goes to standard output and starts with a `## YYYY-MM-DD` heading.

## Work with one day's data in Python

```python
from pathlib import Path
from diary_data import day_at, load_dataset

day = day_at(load_dataset(Path("michigan-trip-diary.csv")), 0)
print(day.date, day.start.name, day.bicycle_miles)
print(day.county_seats, day.weather.sky, day.daylight.sunset)
```

`day_at` returns a `DiaryDay` with a date, start and end locations, ride facts, county-seat stops, weather, and daylight. Distances and measurements use `Decimal`, and sunrise and sunset use Python `time` values. Missing observations are `None`. To render an object you already have, pass it to `render_day` in `render_diary_entry.py`.

## Generate the full diary

The following command writes all entries in date order to `michigan-trip-diary.md`.

```bash
python3 render_trip_diary.py
```

The script reads `michigan-trip-diary.csv`, adds a factual preface, and closes with a postscript whose trip totals come from the dataset. Pass another CSV or JSON export as the first argument to use a different dataset. Use `--output` to write to a different Markdown file.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```
