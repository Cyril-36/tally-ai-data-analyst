"""How periods read to people, shared by sentences and caveats so they never disagree."""

from datetime import date


def period_label(value, grain) -> str:
    """ISO period start as people read it: Jan 2026, Q1 2026, 2026, week of 3 Feb 2026."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    if grain == "month":
        return day.strftime("%b %Y")
    if grain == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    if grain == "year":
        return str(day.year)
    if grain == "week":
        return f"week of {day_label(day)}"
    return day_label(day)


def day_label(day: date) -> str:
    return f"{day.day} {day.strftime('%b %Y')}"
