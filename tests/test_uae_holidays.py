from datetime import date, timedelta

from scripts.generate_uae_holidays import build_holidays


def test_uae_holiday_generator_includes_fixed_and_hijri_dates() -> None:
    holidays = build_holidays(2027)

    assert date(2027, 1, 1) in holidays
    assert date(2027, 12, 2) in holidays
    assert date(2027, 12, 3) in holidays

    eid_days = [
        day for day, names in holidays.items()
        if any("Eid Al Fitr" in name for name in names)
    ]
    assert len(eid_days) == 3
    assert max(eid_days) - min(eid_days) == timedelta(days=2)
    assert all(day.year == 2027 for day in holidays)


def test_global_and_per_holiday_adjustments_shift_islamic_dates() -> None:
    base = build_holidays(2027)
    shifted = build_holidays(2027, global_adjustment=1)
    fitr_base = [
        day for day, names in base.items()
        if any("Eid Al Fitr" in name for name in names)
    ]
    fitr_shifted = [
        day for day, names in shifted.items()
        if any("Eid Al Fitr" in name for name in names)
    ]
    assert fitr_shifted == [day + timedelta(days=1) for day in fitr_base]

    per_rule = build_holidays(2027, adjustments={"eid_al_fitr": -1})
    fitr_per_rule = [
        day for day, names in per_rule.items()
        if any("Eid Al Fitr" in name for name in names)
    ]
    assert fitr_per_rule == [day - timedelta(days=1) for day in fitr_base]
