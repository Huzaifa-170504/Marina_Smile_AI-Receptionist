from __future__ import annotations

import argparse
from datetime import date, timedelta

try:
    from hijridate import Gregorian, Hijri
except ImportError:  # pragma: no cover - developer helper
    raise SystemExit(
        "Install the Hijri calendar helper first: "
        "pip install 'hijridate>=2.3,<3.0'"
    )


# The rule set is stable; only the Gregorian dates change each year.
HIJRI_RULES = (
    {
        "key": "eid_al_fitr",
        "name": "Eid Al Fitr",
        "month": 10,
        "day": 1,
        "duration": 3,
    },
    {
        "key": "arafah",
        "name": "Arafah Day",
        "month": 12,
        "day": 9,
        "duration": 1,
    },
    {
        "key": "eid_al_adha",
        "name": "Eid Al Adha",
        "month": 12,
        "day": 10,
        "duration": 3,
    },
    {
        "key": "islamic_new_year",
        "name": "Islamic New Year",
        "month": 1,
        "day": 1,
        "duration": 1,
    },
    {
        "key": "prophet_birthday",
        "name": "Prophet Muhammad's Birthday",
        "month": 3,
        "day": 12,
        "duration": 1,
    },
)

GREGORIAN_RULES = (
    {"key": "new_year", "name": "New Year's Day", "month": 1, "day": 1, "duration": 1},
    {"key": "uae_national_day", "name": "UAE National Day", "month": 12, "day": 2, "duration": 2},
)


def _hijri_year_range(year: int) -> range:
    start = Gregorian(year, 1, 1).to_hijri().year
    end = Gregorian(year, 12, 31).to_hijri().year
    return range(start, end + 1)


def _parse_adjustments(values: list[str]) -> dict[str, int]:
    adjustments: dict[str, int] = {}
    for value in values:
        key, _, offset = value.partition("=")
        if not key or not offset:
            raise SystemExit("Use --adjust key=days, for example --adjust eid_al_fitr=1")
        try:
            adjustments[key] = int(offset)
        except ValueError as exc:
            raise SystemExit(f"Invalid adjustment: {value}") from exc
    return adjustments


def build_holidays(
    year: int,
    *,
    global_adjustment: int = 0,
    adjustments: dict[str, int] | None = None,
) -> dict[date, list[str]]:
    adjustments = adjustments or {}
    holidays: dict[date, list[str]] = {}

    def add(day: date, name: str) -> None:
        if day.year != year:
            return
        holidays.setdefault(day, []).append(name)

    for rule in GREGORIAN_RULES:
        start = date(year, rule["month"], rule["day"])
        for offset in range(rule["duration"]):
            label = rule["name"]
            if rule["duration"] > 1:
                label = f"{label} (day {offset + 1})"
            add(start + timedelta(days=offset), label)

    for rule in HIJRI_RULES:
        adjustment = global_adjustment + adjustments.get(rule["key"], 0)
        for hijri_year in _hijri_year_range(year):
            base = Hijri(hijri_year, rule["month"], rule["day"]).to_gregorian()
            start = date(base.year, base.month, base.day) + timedelta(days=adjustment)
            for offset in range(rule["duration"]):
                label = rule["name"]
                if rule["duration"] > 1:
                    label = f"{label} (day {offset + 1})"
                add(start + timedelta(days=offset), label)

    return holidays


def render_sql(
    holidays: dict[date, list[str]],
    *,
    clinic_id: str,
    year: int,
    global_adjustment: int,
    adjustments: dict[str, int],
) -> str:
    lines = [
        f"-- Provisional UAE public holidays for {year}.",
        "-- Islamic dates are calculated from the Hijri calendar and are",
        "-- provisional until the UAE officially announces the moon-sighting result.",
        "-- Review these dates before applying.",
        f"-- Global Islamic adjustment: {global_adjustment:+d} day(s).",
    ]
    if adjustments:
        lines.append(
            "-- Per-holiday adjustment: "
            + ", ".join(f"{key}={value:+d}" for key, value in sorted(adjustments.items()))
        )
    lines.append("")
    for day in sorted(holidays):
        name = " / ".join(dict.fromkeys(holidays[day])).replace("'", "''")
        lines.append(
            "insert into public.clinic_holidays (clinic_id, holiday_date, name)\n"
            f"values ('{clinic_id}', '{day.isoformat()}', '{name}')\n"
            "on conflict (clinic_id, holiday_date) do update set name = excluded.name;"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate provisional UAE public-holiday SQL for clinic_holidays. "
            "Always verify Islamic dates against the official UAE announcement."
        )
    )
    parser.add_argument("--year", type=int, required=True, help="Gregorian year to generate")
    parser.add_argument(
        "--clinic-id",
        default="<clinic-uuid>",
        help="Clinic UUID (default: placeholder)",
    )
    parser.add_argument(
        "--adjust-days",
        type=int,
        default=0,
        help="Shift all Hijri-derived dates by N days (moon-sighting adjustment)",
    )
    parser.add_argument(
        "--adjust",
        action="append",
        default=[],
        metavar="KEY=DAYS",
        help="Per-holiday Hijri adjustment, e.g. --adjust eid_al_fitr=1",
    )
    parser.add_argument(
        "--output",
        help="Write SQL to this file instead of stdout",
    )
    args = parser.parse_args()

    adjustments = _parse_adjustments(args.adjust)
    holidays = build_holidays(
        args.year,
        global_adjustment=args.adjust_days,
        adjustments=adjustments,
    )
    sql = render_sql(
        holidays,
        clinic_id=args.clinic_id,
        year=args.year,
        global_adjustment=args.adjust_days,
        adjustments=adjustments,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(sql)
        print(f"Wrote {args.output}")
    else:
        print(sql)


if __name__ == "__main__":
    main()
