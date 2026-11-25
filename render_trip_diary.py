#!/usr/bin/env python3
"""Write the factual trip diary with all entries in date order."""

import argparse
from collections.abc import Mapping, Sequence
import csv
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from diary_data import DiaryDay, day_at, load_dataset
from render_diary_entry import location_phrase, render_day
from utils import display_number, pluralize, bottles_of_beer_song


PREFACE = (
    "# Visiting Every County Seat in Michigan\n\n"
    "## Preface\n\n"
    "In May, 2026, I embarked on a journey to visit every county seat in Michigan by bicycle."
    "This is the diary of that journey. At least, as I imagined it."
)


def render_trip(dataset: Sequence[Mapping | DiaryDay]) -> str:
    """Reuse the single-entry renderer and summarize the trip from the same records."""
    if not dataset:
        raise ValueError("Dataset has no diary records.")
    days = sorted(
        (day_at(dataset, offset) for offset in range(len(dataset))),
        key=lambda day: day.date,
    )
    first, last = days[0], days[-1]
    departure = location_phrase(first.start.name, "in")
    arrival = location_phrase(last.end.name, "in")
    bicycle_miles = sum(day.bicycle_miles for day in days)
    shuttle_miles = sum(day.shuttle_miles or Decimal(0) for day in days)
    counties = {seat.county for day in days for seat in day.county_seats}
    layovers = sum((day.status or "").lower() == "rain layover" for day in days)

    summary = [
        f"The trip began {departure} on {first.date} and ended {arrival} on {last.date}."
    ]
    day_unit = pluralize(len(days), "day")
    miles = pluralize(bicycle_miles, "mile")
    summary.append(
        f"Over {len(days)} diary {day_unit}, the rider logged {display_number(bicycle_miles)} bicycle {miles}."
    )
    if counties:
        seats = pluralize(len(counties), "seat")
        summary.append(f"The itinerary reached {len(counties)} county {seats}.")
    if layovers:
        layover_unit = pluralize(layovers, "layover")
        summary.append(f"It included {layovers} heavy-rain {layover_unit}.")
    if shuttle_miles:
        miles = pluralize(shuttle_miles, "mile")
        summary.append(
            f"I went {display_number(shuttle_miles)} vehicle {miles} for the Mackinac Bridge shuttle."
        )

    body = [
        render_day(days, index).rstrip("\n")
        for index in range(len(days))
    ]
    postscript = "## Postscript\n\n" + " ".join(summary)
    everthing = "\n\n".join([PREFACE, *body, postscript]) + "\n"
    # convert ", MI" to ", Michigan"
    everthing = everthing.replace(", MI", ", Michigan")
    song = (
        "## Song \n\nThis is the song I sang during the trip:  \n\n"
        + bottles_of_beer_song(1999)
    )
    everthing += "\n\n" + song
    return everthing


def write_diary(output: Path, markdown: str) -> None:
    """Replace the output only after the complete diary has been written."""
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=output.parent,
        prefix=f".{output.name}-",
        delete=False,
    ) as file:
        staged = Path(file.name)
        try:
            file.write(markdown)
            file.close()
            if output.exists():
                staged.chmod(output.stat().st_mode & 0o777)
            staged.replace(output)
        finally:
            staged.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=Path("michigan-trip-diary.csv"),
        help="collector CSV or JSON export (default: michigan-trip-diary.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("michigan-trip-diary.md"),
        help="Markdown output path (default: michigan-trip-diary.md)",
    )
    args = parser.parse_args(argv)
    try:
        if args.dataset.resolve() == args.output.resolve():
            raise ValueError("The dataset and output must be different files.")
        rows = load_dataset(args.dataset)
        write_diary(args.output, render_trip(rows))
    except (OSError, ValueError, IndexError, csv.Error) as error:
        parser.error(str(error))
    print(f"Wrote {len(rows)} entries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
