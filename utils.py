"""Small formatting helpers shared by the diary renderers."""

from decimal import Decimal


def pluralize(
    count: int | float | Decimal | str, singular: str, plural: str | None = None
) -> str:
    """Choose a word form for a count, including the displayed count ``"1"``."""
    if count == 1 or count == "1":
        return singular
    return f"{singular}s" if plural is None else plural


def display_number(value: Decimal, places: int = 1) -> str:
    """Format a decimal with grouping and omit trailing fractional zeros."""
    if not value:
        return "0"
    formatted = f"{value:,.{places}f}"
    return formatted.rstrip("0").rstrip(".") if places else formatted


def display_date_with_weekday_and_month(date) -> str:
    """Format a date with the weekday and month No leading zero for the day."""
    return date.strftime("%A, %B %-d, %Y")


def word_count(text: str) -> int:
    """Return the number of words in a string."""
    return len(text.split())


def words_needed(text: str, target: int) -> int:
    """Return the number of additional words needed to reach the target count."""
    return max(0, target - word_count(text))


def bottles_of_beer_on_the_wall(count: int) -> str:
    """Return the 'bottles of beer on the wall' verse for a given count."""
    if count == 0:
        return "No more bottles of beer on the wall, no more bottles of beer."
    bottle_word = pluralize(count, "bottle")
    return (
        f"{count} {bottle_word} of beer on the wall.  \n{count} {bottle_word} of beer."
    )


def bottles_of_beer_song(start: int = 99) -> str:
    """Return the full 'bottles of beer on the wall' song starting from the given count."""
    verses = [bottles_of_beer_on_the_wall(i) for i in range(start, -1, -1)]
    return "  \nTake one down and pass it around–  \n".join(verses)
