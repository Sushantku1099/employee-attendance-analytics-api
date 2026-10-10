#!/usr/bin/env python3
"""
Tests for the real application helpers, without database reads or writes.
Run: python3 static_helper_tests.py
"""
import sys
from datetime import date, datetime, timedelta, timezone
from app.main import (
    IST, UTC, attendance_date, compute_late_minutes, compute_overtime,
    compute_work_hours, dt_to_ms, epoch_ms_to_utc, round_half_up,
)

PASS = 0
FAIL = 0


def check(label, condition, detail: object = ""):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}  → got: {detail}")
        FAIL += 1


print("=== round_half_up ===")
# Decimal avoids the surprises of Python's round() at halfway values.
check("Python round(2.675,2) is banker's → 2.67", round(2.675, 2) == 2.67, round(2.675, 2))
check("round_half_up(2.675,2) = 2.68 (true half-up)", round_half_up(2.675, 2) == 2.68, round_half_up(2.675, 2))
check("round_half_up(9.125,2) = 9.13 (half-up, not banker's)", round_half_up(9.125, 2) == 9.13, round_half_up(9.125, 2))
check("round_half_up(9.115,2) = 9.12", round_half_up(9.115, 2) == 9.12, round_half_up(9.115, 2))
check("round_half_up(0.33325,4) = 0.3333", round_half_up(0.33325, 4) == 0.3333, round_half_up(0.33325, 4))
check("round_half_up(0.33335,4) = 0.3334", round_half_up(0.33335, 4) == 0.3334, round_half_up(0.33335, 4))

print("\n=== epoch_ms_to_utc (truncation) ===")
ms = 1783312500900
dt = epoch_ms_to_utc(ms)
check("microsecond=0 after truncation", dt.microsecond == 0, dt)
check("second=0 (1783312500 % 60 = 0)", dt.second == 0, dt.second)
check("UTC-aware", dt.tzinfo is not None, dt.tzinfo)
ms2 = 1783312500999
dt2 = epoch_ms_to_utc(ms2)
check("999ms truncated same as 000ms", epoch_ms_to_utc(1783312500000) == dt2, (epoch_ms_to_utc(1783312500000), dt2))

print("\n=== attendance_date — normal shift ===")
punch_utc = datetime(2026, 7, 6, 3, 58, 0, tzinfo=UTC)
punch_ist = punch_utc.astimezone(IST)
d = attendance_date(punch_ist, "09:30", "18:30")
check("normal: date is 2026-07-06", d == date(2026, 7, 6), d)

print("\n=== attendance_date — overnight shift (22:00–06:00) ===")
punch_utc = datetime(2026, 7, 6, 16, 25, 0, tzinfo=UTC)
punch_ist = punch_utc.astimezone(IST)
d = attendance_date(punch_ist, "22:00", "06:00")
check("overnight start: 21:55 IST → 2026-07-06", d == date(2026, 7, 6), d)

# A punch before the overnight shift ends belongs to the previous day.
punch_utc = datetime(2026, 7, 7, 0, 20, 0, tzinfo=UTC)  # 05:50 IST
punch_ist = punch_utc.astimezone(IST)
d = attendance_date(punch_ist, "22:00", "06:00")
check("overnight: 05:50 IST on July 7 → date=2026-07-06 (prev day)", d == date(2026, 7, 6), d)

# At exactly shift end, the punch belongs to the new date.
punch_utc = datetime(2026, 7, 7, 0, 30, 0, tzinfo=UTC)  # 06:00 IST
punch_ist = punch_utc.astimezone(IST)
d = attendance_date(punch_ist, "22:00", "06:00")
check("overnight: exactly 06:00 IST on July 7 → date=2026-07-07 (new shift)", d == date(2026, 7, 7), d)

punch_utc = datetime(2026, 7, 7, 16, 45, 0, tzinfo=UTC)  # 22:15 IST
punch_ist = punch_utc.astimezone(IST)
d = attendance_date(punch_ist, "22:00", "06:00")
check("overnight: 22:15 IST → date=2026-07-07 (shift starts)", d == date(2026, 7, 7), d)

print("\n=== compute_late_minutes (R2) ===")
att_day = date(2026, 7, 6)
shift = "09:30"

pi = datetime(2026, 7, 6, 9, 28, 0, tzinfo=IST)
check("09:28 IST → late=0 (early)", compute_late_minutes(pi, shift, att_day) == 0, compute_late_minutes(pi, shift, att_day))

pi = datetime(2026, 7, 6, 9, 40, 0, tzinfo=IST)
check("09:40:00 IST → late=0 (exactly 600s, boundary)", compute_late_minutes(pi, shift, att_day) == 0, compute_late_minutes(pi, shift, att_day))

# Once the grace period is exceeded, count minutes from shift start.
pi = datetime(2026, 7, 6, 9, 40, 1, tzinfo=IST)
check("09:40:01 IST → late=10 (R2 example)", compute_late_minutes(pi, shift, att_day) == 10, compute_late_minutes(pi, shift, att_day))

pi = datetime(2026, 7, 6, 10, 5, 0, tzinfo=IST)
check("10:05 IST → late=35 (sample EMP0003)", compute_late_minutes(pi, shift, att_day) == 35, compute_late_minutes(pi, shift, att_day))

pi = datetime(2026, 7, 6, 10, 15, 59, tzinfo=IST)
check("10:15:59 IST → late=45 (R2 example)", compute_late_minutes(pi, shift, att_day) == 45, compute_late_minutes(pi, shift, att_day))

print("\n=== compute_work_hours (R4) ===")
pi = datetime(2026, 7, 6, 3, 58, 0, tzinfo=UTC)
po = datetime(2026, 7, 6, 13, 5, 0, tzinfo=UTC)
wh = compute_work_hours(pi, po)
check("EMP0001 sample: 9h7m = 9.12", wh == 9.12, wh)

pi = datetime(2026, 7, 6, 4, 35, 0, tzinfo=UTC)
po = datetime(2026, 7, 6, 13, 40, 0, tzinfo=UTC)
wh = compute_work_hours(pi, po)
check("EMP0003 sample: 9h5m = 9.08", wh == 9.08, wh)

pi = datetime(2026, 7, 7, 4, 0, 0, tzinfo=UTC)
po = datetime(2026, 7, 7, 7, 30, 0, tzinfo=UTC)
wh = compute_work_hours(pi, po)
check("EMP0004 half-day: 3.5h", wh == 3.5, wh)

print("\n=== compute_overtime (R3) ===")
att_day = date(2026, 7, 6)

po_ist = datetime(2026, 7, 6, 19, 10, 0, tzinfo=IST)
ot = compute_overtime(po_ist, "09:30", "18:30", att_day)
check("EMP0003: 40 min overtime", ot == 40, ot)

po_ist = datetime(2026, 7, 6, 18, 35, 0, tzinfo=IST)
ot = compute_overtime(po_ist, "09:30", "18:30", att_day)
check("EMP0001: 5 min overtime → 0 (below 30 min threshold)", ot == 0, ot)

# The overnight shift ends on the next day.
po_ist = datetime(2026, 7, 7, 6, 40, 0, tzinfo=IST)
ot = compute_overtime(po_ist, "22:00", "06:00", date(2026, 7, 6))
check("EMP0005 overnight: 40 min overtime", ot == 40, ot)

po_ist = datetime(2026, 7, 6, 19, 0, 0, tzinfo=IST)
ot = compute_overtime(po_ist, "09:30", "18:30", att_day)
check("Exactly 30 min overtime → 30 (≥1800s)", ot == 30, ot)

po_ist = datetime(2026, 7, 6, 18, 59, 0, tzinfo=IST)
ot = compute_overtime(po_ist, "09:30", "18:30", att_day)
check("29 min overtime → 0 (<1800s)", ot == 0, ot)

print("\n=== dt_to_ms / epoch_ms_to_utc round-trip ===")
ms_in = 1783312500000
dt = epoch_ms_to_utc(ms_in)
ms_out = dt_to_ms(dt)
check("round-trip preserves epoch ms", ms_in == ms_out, (ms_in, ms_out))
check("dt_to_ms(None) = None", dt_to_ms(None) is None, dt_to_ms(None))

from unittest.mock import patch
from fastapi import HTTPException
from pymongo.errors import ServerSelectionTimeoutError
from app.main import health, serialize_record

legacy = serialize_record(dict(emp_code="EMP0001", date="2026-07-06", status="ABSENT"))
check("legacy missing fields use defaults", legacy["history"] == [] and
      legacy["half_day"] is False and legacy["late_minutes"] == 0 and
      legacy["overtime_minutes"] == 0)
instant = datetime(2026, 7, 6, tzinfo=UTC)
record = serialize_record(dict(emp_code="EMP0001", date="2026-07-06", status="PRESENT",
    _id="internal", punch_in=instant, history=[dict(at=instant, by="HR", reason="fix",
    changes={"punch_in": {"from": None, "to": instant}})]))
check("nested history timestamps use milliseconds", record["history"][0]["at"] == dt_to_ms(instant)
      and record["history"][0]["changes"]["punch_in"] == {"from": None, "to": dt_to_ms(instant)})
check("serializer omits MongoDB id", "_id" not in record)
with patch("app.main.client") as mock_client:
    mock_client.admin.command.side_effect = ServerSelectionTimeoutError("test outage")
    try:
        health()
        check("database outage returns 503", False)
    except HTTPException as error:
        check("database outage returns 503", error.status_code == 503 and error.detail == "database unavailable")
po = datetime(2026, 7, 6, 18, 59, 59, tzinfo=IST)
check("29m59s overtime is zero", compute_overtime(po, "09:30", "18:30", att_day) == 0)
pi = datetime(2026, 7, 6, tzinfo=UTC)
check("work hours midpoint rounds up", compute_work_hours(pi, pi + timedelta(seconds=162)) == 0.05)

from app.main import PunchInIn
from pydantic import ValidationError

check("omitted timestamp uses server-time default", PunchInIn(emp_code="EMP0001").punched_at is None)
try:
    PunchInIn(emp_code="EMP0001", punched_at=None)
    check("explicit null timestamp rejected", False)
except ValidationError:
    check("explicit null timestamp rejected", True)

print(f"\n{'='*40}")
print(f"Results: {PASS} PASS  {FAIL} FAIL")
if FAIL:
    sys.exit(1)
