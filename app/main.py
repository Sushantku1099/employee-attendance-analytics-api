"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded automatically if present)
"""
import os
import json
from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal, NoReturn, Optional, Union

from bson import Decimal128, json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path as PathParameter, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

# Shift times and attendance dates use India time, 5 hours 30 minutes ahead of UTC.
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# Keep the UTC timezone on dates read from MongoDB.
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MongoDB prevents duplicate employee codes, even when requests arrive together.
    db.employees.create_index("emp_code", unique=True)

    # An employee can have only one attendance record per day.
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True
    )

    # This index follows the order used by the attendance list.
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])

    # Date-range reports use this index before grouping logs.
    db.attendance_logs.create_index("date")

    # Punch-out looks up the employee's latest punch before the requested time.
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)])

    # Reports filter people by joining date, with or without a department.
    db.employees.create_index("joined_on")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])

    yield


app = FastAPI(
    title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan
)


# Reject floats, strings, seconds-sized values and timestamps outside the allowed range.
EpochMillisField = Annotated[
    int, Field(strict=True, ge=100_000_000_000, le=4_102_444_800_000, json_schema_extra={"format": "int64"})
]


def round_half_up(value: float, places: int) -> float:
    """Round halfway values up, so 9.125 becomes 9.13 rather than 9.12."""
    quantizer = Decimal("0." + "0" * places)
    return float(Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP))


def epoch_ms_to_utc(ms: int) -> datetime:
    """Drop the milliseconds so stored times are accurate to whole seconds."""
    return datetime.fromtimestamp(ms // 1000, tz=UTC)


def dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Return milliseconds since 1970, or None when there is no time."""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def attendance_date(punch_in_ist: datetime, shift_start_str: str, shift_end_str: str) -> date:
    """An overnight punch before shift end belongs to the previous day."""
    start_hour, start_minute = map(int, shift_start_str.split(":"))
    end_hour, end_minute = map(int, shift_end_str.split(":"))
    shift_is_overnight = (end_hour * 60 + end_minute) <= (start_hour * 60 + start_minute)

    if shift_is_overnight:
        shift_end_time = dtime(end_hour, end_minute, 0)
        if punch_in_ist.time().replace(microsecond=0) < shift_end_time:
            return (punch_in_ist - timedelta(days=1)).date()

    return punch_in_ist.date()


def compute_late_minutes(punch_in_ist: datetime, shift_start_str: str, attendance_day: date) -> int:
    """Exactly 10 minutes is allowed; after that, count minutes from shift start."""
    start_hour, start_minute = map(int, shift_start_str.split(":"))
    shift_start_dt = datetime(attendance_day.year, attendance_day.month, attendance_day.day, start_hour, start_minute, 0, tzinfo=IST)
    total_seconds = (punch_in_ist - shift_start_dt).total_seconds()
    if total_seconds > 600:
        return int(total_seconds / 60)
    return 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Return hours worked, rounded to two decimal places with halfway values up."""
    raw = (punch_out - punch_in).total_seconds() / 3600
    return round_half_up(raw, 2)


def compute_overtime(
    punch_out_ist: datetime,
    shift_start_str: str,
    shift_end_str: str,
    attendance_day: date,
) -> int:
    """Count overtime only after at least 30 minutes past shift end.

    An overnight shift ends on the day after its attendance date.
    """
    start_hour, start_minute = map(int, shift_start_str.split(":"))
    end_hour, end_minute = map(int, shift_end_str.split(":"))
    shift_is_overnight = (end_hour * 60 + end_minute) <= (start_hour * 60 + start_minute)

    if shift_is_overnight:
        shift_end_dt = (
            datetime(attendance_day.year, attendance_day.month, attendance_day.day, end_hour, end_minute, 0, tzinfo=IST)
            + timedelta(days=1)
        )
    else:
        shift_end_dt = datetime(attendance_day.year, attendance_day.month, attendance_day.day, end_hour, end_minute, 0, tzinfo=IST)

    total_seconds = (punch_out_ist - shift_end_dt).total_seconds()
    if total_seconds >= 1800:
        return int(total_seconds / 60)
    return 0


def serialize_history_entry(entry: dict) -> dict:
    """Convert history times to milliseconds, including old and new punch times."""
    changes = {}
    for field, change in entry.get("changes", {}).items():
        if field in ("punch_in", "punch_out"):
            changes[field] = {
                "from": dt_to_ms(change.get("from")),
                "to": dt_to_ms(change.get("to")),
            }
        else:
            changes[field] = change

    return {
        "at": dt_to_ms(entry["at"]),
        "by": entry["by"],
        "reason": entry["reason"],
        "changes": changes,
    }


def serialize_record(doc: dict) -> dict:
    """Build an API record without MongoDB's internal ID.

    Older records may lack history or calculated fields, so use their defaults.
    """
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": dt_to_ms(doc.get("punch_in")),
        "punch_out": dt_to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": [serialize_history_entry(h) for h in doc.get("history", [])],
    }


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(json_schema_extra={"format": "date"})

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def shift_times_must_differ(self) -> "EmployeeIn":
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Union[EpochMillisField, SkipJsonSchema[None]] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at", mode="before")
    @classmethod
    def reject_null_timestamp(cls, value):
        # Leaving out the timestamp uses the server clock; sending null is invalid.
        if value is None:
            raise ValueError("punched_at cannot be null")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Union[EpochMillisField, SkipJsonSchema[None]] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def reject_null_timestamp(cls, value):
        if value is None:
            raise ValueError("punched_at cannot be null")
        return value


class RegularizeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Union[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"], SkipJsonSchema[None]] = None
    punch_in: Union[EpochMillisField, SkipJsonSchema[None]] = None
    punch_out: Union[EpochMillisField, SkipJsonSchema[None]] = None

    @field_validator("status", "punch_in", "punch_out", mode="before")
    @classmethod
    def reject_null_changes(cls, value):
        if value is None:
            raise ValueError("correction fields cannot be null")
        return value


# These models describe the API docs; the serializers above build the responses.
class HealthResponse(BaseModel):
    status: Literal["ok"]


class ErrorResponse(BaseModel):
    detail: str


class ValidationErrorResponse(BaseModel):
    detail: list[dict[str, Any]]


class Employee(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str = Field(json_schema_extra={"format": "date"})
    created_at: EpochMillisField = Field(json_schema_extra={"readOnly": True})


class EmployeePage(BaseModel):
    items: list[Employee]
    total: int
    page: int
    page_size: int


class HistoryChange(BaseModel):
    # Old and new values can be times, numbers, text or null.
    from_value: Any = Field(default=None, alias="from")
    to: Any = None


class HistoryEntry(BaseModel):
    at: EpochMillisField
    by: str
    reason: str
    changes: dict[str, HistoryChange]


class AttendanceRecord(BaseModel):
    emp_code: str
    date: str = Field(json_schema_extra={"format": "date"})
    status: Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    punch_in: Optional[EpochMillisField]
    punch_out: Optional[EpochMillisField]
    work_hours: Optional[float] = Field(json_schema_extra={"readOnly": True})
    late_minutes: int = Field(json_schema_extra={"readOnly": True})
    overtime_minutes: int = Field(json_schema_extra={"readOnly": True})
    half_day: bool = Field(json_schema_extra={"readOnly": True})
    history: list[HistoryEntry]


class AttendancePage(BaseModel):
    items: list[AttendanceRecord]
    total: int
    page: int
    page_size: int


VALIDATION_RESPONSE = {"model": ValidationErrorResponse, "description": "Validation error (FastAPI default body)"}
ERROR_RESPONSE = {"model": ErrorResponse, "description": "Error"}


@app.get("/health", responses={200: {"model": HealthResponse}, 503: ERROR_RESPONSE})
def health():
    """Check that MongoDB is available before reporting the app as ready."""
    try:
        client.admin.command("ping")
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201, responses={
    201: {"model": Employee}, 409: ERROR_RESPONSE, 422: VALIDATION_RESPONSE
})
def create_employee(body: EmployeeIn):
    """Create an employee, or return 409 if the code already exists."""
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = dt_to_ms(doc["created_at"])
    return doc


@app.get("/employees", responses={200: {"model": EmployeePage}, 422: VALIDATION_RESPONSE})
def list_employees(
    department: Union[str, SkipJsonSchema[None]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List employees by code, with the total for the selected department."""
    filters: dict = {}
    if department is not None:
        filters["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(filters)
    items = list(
        db.employees.find(filters, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )
    for emp in items:
        emp["created_at"] = dt_to_ms(emp.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201, responses={
    201: {"model": AttendanceRecord}, 404: ERROR_RESPONSE,
    409: ERROR_RESPONSE, 422: VALIDATION_RESPONSE
})
def punch_in(body: PunchInIn):
    """Record a punch-in; MongoDB rejects duplicates even if requests arrive together."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is not None:
        punch_utc = epoch_ms_to_utc(body.punched_at)
    else:
        punch_utc = datetime.now(UTC).replace(microsecond=0)

    punch_ist = punch_utc.astimezone(IST)
    attendance_day = attendance_date(punch_ist, emp["shift_start"], emp["shift_end"])
    date_str = attendance_day.isoformat()

    late = compute_late_minutes(punch_ist, emp["shift_start"], attendance_day)

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return serialize_record(doc)


def parse_query_date(value: Optional[str], parameter: str = "date") -> Optional[date]:
    """Accept real calendar dates written as YYYY-MM-DD."""
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD")
        return parsed
    except ValueError:
        raise HTTPException(422, [{
            "loc": ["query", parameter],
            "msg": "date must be a valid YYYY-MM-DD date",
            "type": "value_error",
        }])


def attendance_find_operation(emp_code, date_from, date_to, status, page, page_size):
    date_from = parse_query_date(date_from, "date_from")
    date_to = parse_query_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, [{
            "loc": ["query", "date_to"],
            "msg": "date_from must not be after date_to",
            "type": "value_error",
        }])

    filters: dict = {}
    if emp_code is not None:
        filters["emp_code"] = emp_code
    if date_from or date_to:
        filters["date"] = {}
        if date_from:
            filters["date"]["$gte"] = date_from.isoformat()
        if date_to:
            filters["date"]["$lte"] = date_to.isoformat()
    if status:
        filters["status"] = status

    return {"find": "attendance_logs", "filter": filters, "projection": {"_id": 0},
            "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size, "limit": page_size,
            "hint": "date_-1_emp_code_1" if emp_code is None else "emp_code_1_date_1"}


@app.get("/attendance", responses={200: {"model": AttendancePage}, 422: VALIDATION_RESPONSE})
def list_attendance(
    emp_code: Union[str, SkipJsonSchema[None]] = None,
    date_from: Union[str, SkipJsonSchema[None]] = Query(default=None, json_schema_extra={"format": "date"}),
    date_to: Union[str, SkipJsonSchema[None]] = Query(default=None, json_schema_extra={"format": "date"}),
    status: Union[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"], SkipJsonSchema[None]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List newest dates first, then employee codes in ascending order.

    MongoDB sorts and selects the page so we do not load every record.
    """
    operation = attendance_find_operation(emp_code, date_from, date_to, status, page, page_size)
    total = db.attendance_logs.count_documents(operation["filter"])
    docs = list(
        db.attendance_logs.find(operation["filter"], operation["projection"])
        .sort(list(operation["sort"].items()))
        .skip(operation["skip"]).limit(operation["limit"]).hint(operation["hint"])
    )
    return {
        "items": [serialize_record(d) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def attendance_validation_error(field: str, message: str) -> NoReturn:
    raise HTTPException(422, [{"loc": ["body", field], "msg": message, "type": "value_error"}])


def validate_punch_duration(punch_in: datetime, punch_out: datetime, field: str = "punch_out"):
    duration = (punch_out - punch_in).total_seconds()
    if duration <= 0 or duration > 24 * 60 * 60:
        attendance_validation_error(field, "punch_out must be after punch_in and within 24 hours")


def update_attendance(record: dict, values: dict, history_entry: Optional[dict] = None) -> dict:
    """Save only if the record still matches what we read."""
    filters = {"emp_code": record["emp_code"], "date": record["date"]}
    # Missing and null are different in MongoDB, so match missing fields explicitly.
    for field in ("status", "punch_in", "punch_out", "work_hours", "late_minutes",
                  "overtime_minutes", "half_day", "history"):
        filters[field] = {"$eq": record[field], "$exists": True} if field in record else {"$exists": False}
    update = {"$set": values}
    if history_entry is not None:
        # Save the correction and its audit entry together; never replace history.
        update["$push"] = {"history": history_entry}
    updated = db.attendance_logs.find_one_and_update(
        filters, update, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        raise HTTPException(409, "attendance changed; read it again before retrying")
    return updated


@app.post("/attendance/punch-out", responses={
    200: {"model": AttendanceRecord}, 404: ERROR_RESPONSE,
    409: ERROR_RESPONSE, 422: VALIDATION_RESPONSE
})
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")
    if body.punched_at is not None:
        punch_utc = epoch_ms_to_utc(body.punched_at)
    else:
        punch_utc = datetime.now(UTC).replace(microsecond=0)
    # Include closed records: a second punch-out must return 409, not close an older day.
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punch_utc}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "punch-in not found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
    punch_in_utc = record["punch_in"].replace(microsecond=0)
    validate_punch_duration(punch_in_utc, punch_utc, "punched_at")
    work_hours = compute_work_hours(punch_in_utc, punch_utc)
    values = {
        "punch_out": punch_utc,
        "work_hours": work_hours,
        "overtime_minutes": compute_overtime(
            punch_utc.astimezone(IST), employee["shift_start"], employee["shift_end"],
            date.fromisoformat(record["date"]),
        ),
        "half_day": work_hours < 4.50,
    }
    return serialize_record(update_attendance(record, values))


@app.patch("/attendance/{emp_code}/{date}", responses={
    200: {"model": AttendanceRecord}, 404: ERROR_RESPONSE,
    409: ERROR_RESPONSE, 422: VALIDATION_RESPONSE
})
def regularize_attendance(
    emp_code: str,
    body: RegularizeIn,
    date_str: str = PathParameter(alias="date", json_schema_extra={"format": "date"}),
):
    try:
        attendance_day = date.fromisoformat(date_str)
        if attendance_day.isoformat() != date_str:
            raise ValueError("date must use YYYY-MM-DD")
    except ValueError:
        raise HTTPException(422, [{
            "loc": ["path", "date"], "msg": "date must be a valid YYYY-MM-DD date", "type": "value_error",
        }])
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_str})
    if record is None:
        raise HTTPException(404, "attendance not found")

    # Treat omitted legacy fields like their documented defaults when comparing changes.
    before = {
        "status": record["status"], "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"), "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    after = before.copy()
    supplied = body.model_fields_set
    if "status" in supplied:
        after["status"] = body.status
    for field in ("punch_in", "punch_out"):
        if field in supplied:
            after[field] = epoch_ms_to_utc(getattr(body, field))
        elif after[field] is not None:
            after[field] = after[field].replace(microsecond=0)

    if after["status"] in ("ABSENT", "LEAVE"):
        if supplied.intersection({"punch_in", "punch_out"}):
            attendance_validation_error("status", "ABSENT and LEAVE cannot include punch times")
        after.update(punch_in=None, punch_out=None, work_hours=None,
                     late_minutes=0, overtime_minutes=0, half_day=False)
    else:
        punch_in = after["punch_in"]
        if not isinstance(punch_in, datetime):
            attendance_validation_error("punch_in", "a presence status requires punch_in")
        punch_in_ist = punch_in.astimezone(IST)
        if attendance_date(punch_in_ist, employee["shift_start"], employee["shift_end"]) != attendance_day:
            attendance_validation_error("punch_in", "punch_in must stay on the attendance date")
        after["late_minutes"] = compute_late_minutes(punch_in_ist, employee["shift_start"], attendance_day)
        punch_out = after["punch_out"]
        if punch_out is None:
            after.update(work_hours=None, overtime_minutes=0, half_day=False)
        else:
            if not isinstance(punch_out, datetime):
                attendance_validation_error("punch_out", "punch_out must be a valid timestamp")
            validate_punch_duration(punch_in, punch_out)
            after["work_hours"] = compute_work_hours(punch_in, punch_out)
            after["overtime_minutes"] = compute_overtime(
                punch_out.astimezone(IST), employee["shift_start"], employee["shift_end"], attendance_day,
            )
            after["half_day"] = after["work_hours"] < 4.50

    changes = {
        field: {"from": before[field], "to": value}
        for field, value in after.items() if before[field] != value
    }
    # Compare the final values, including recalculated fields, before adding history.
    if not changes:
        attendance_validation_error("body", "correction must change the attendance record")
    history_entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.regularized_by, "reason": body.reason, "changes": changes,
    }
    return serialize_record(update_attendance(record, after, history_entry))


class EmployeeMonthly(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float]


class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float]
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int


class DepartmentSummary(BaseModel):
    month: str
    items: list[DepartmentSummaryItem]


class LeaderboardItem(BaseModel):
    rank: int = Field(ge=1)
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int


class Leaderboard(BaseModel):
    month: str
    items: list[LeaderboardItem]


class TrendItem(BaseModel):
    date: str = Field(json_schema_extra={"format": "date"})
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float]
    moving_avg_7d: Optional[float]


class Trend(BaseModel):
    department: str
    items: list[TrendItem]


class ExplainResponse(BaseModel):
    endpoint: str
    collection: str
    explain: dict[str, Any]


MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
PRESENCE_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]


def query_error(parameter: str, message: str) -> NoReturn:
    raise HTTPException(422, [{"loc": ["query", parameter], "msg": message, "type": "value_error"}])


def month_bounds(month: str):
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError:
        query_error("month", "month must be a valid YYYY-MM month")
    return first, date(first.year, first.month, monthrange(first.year, first.month)[1])


def trend_bounds(start: Optional[str], end: Optional[str]):
    first = parse_query_date(start, "from")
    last = parse_query_date(end, "to")
    if first is None:
        query_error("from", "field required")
    if last is None:
        query_error("to", "field required")
    if last < first or (last - first).days + 1 > 92:
        query_error("to", "range must contain between 1 and 92 calendar days")
    return first, last


def mongo_half_up(expression, places: int):
    # MongoDB's $round uses ties-to-even. Decimal arithmetic gives the required half-up result.
    scale = 10 ** places
    return {"$let": {"vars": {"number": {"$toDecimal": expression}}, "in": {
        "$cond": [{"$eq": ["$$number", None]}, None, {"$toDouble": {"$divide": [
            {"$multiply": [
                {"$cond": [{"$lt": ["$$number", 0]}, -1, 1]},
                {"$floor": {"$add": [{"$multiply": [{"$abs": "$$number"}, scale]}, Decimal128("0.5")]}}
            ]}, scale
        ]}}]
    }}}


def calendar_day(value):
    # These temporary dates are only for calendar arithmetic, not stored punch times.
    return {"$dateFromString": {"dateString": value, "format": "%Y-%m-%d", "timezone": "UTC"}}


def weekday(value):
    return {"$lte": [{"$isoDayOfWeek": calendar_day(value)}, 5]}


def log_statistics():
    presence = {"$in": ["$status", PRESENCE_STATUSES]}
    present_weight = {"$cond": [presence, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}
    has_hours = {"$and": [presence, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}
    return [{"$group": {
        "_id": None,
        "present_days": {"$sum": {"$cond": [weekday("$date"), present_weight, 0]}},
        "present_count": {"$sum": present_weight},
        "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        "hours_sum": {"$sum": {"$cond": [has_hours, {"$toDecimal": "$work_hours"}, 0]}},
        "hours_count": {"$sum": {"$cond": [has_hours, 1, 0]}},
    }}]


def monthly_logs(first: date, last: date):
    return {"$lookup": {
        "from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code",
        "pipeline": [{"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}}] + log_statistics(),
        "as": "stats",
    }}


def statistic(name: str):
    return {"$ifNull": [{"$arrayElemAt": ["$stats." + name, 0]}, 0]}


def employee_monthly_pipeline(emp_code: str, month: str):
    first, last = month_bounds(month)
    days = {"$map": {"input": {"$range": [0, last.day]}, "as": "offset", "in": {
        "$dateToString": {"date": {"$dateAdd": {"startDate": calendar_day(first.isoformat()),
            "unit": "day", "amount": "$$offset"}}, "format": "%Y-%m-%d"}
    }}}
    return [
        {"$match": {"emp_code": emp_code}}, monthly_logs(first, last),
        {"$set": {"working_days": {"$size": {"$filter": {"input": days, "as": "day", "cond": {
            "$and": [{"$gte": ["$$day", "$joined_on"]}, weekday("$$day")]
        }}}}, "present_days": statistic("present_days")}},
        {"$project": {
            "_id": 0, "emp_code": 1, "month": {"$literal": month}, "working_days": 1, "present_days": 1,
            "leave_days": statistic("leave_count"), "late_count": statistic("late_count"),
            "total_late_minutes": statistic("total_late_minutes"),
            "total_overtime_minutes": statistic("total_overtime_minutes"),
            "attendance_pct": {"$cond": [{"$gt": ["$working_days", 0]}, mongo_half_up(
                {"$divide": [{"$multiply": [{"$toDecimal": "$present_days"}, 100]}, "$working_days"]}, 2), None]},
        }}
    ]


def department_summary_pipeline(month: str, department: Optional[str]):
    first, last = month_bounds(month)
    filters: dict[str, Any] = {"joined_on": {"$lte": last.isoformat()}}
    if department is not None:
        filters["department"] = department
    # Sum hours and record counts separately so employees with fewer logs get no extra weight.
    group = {"_id": "$department", "headcount": {"$sum": 1}}
    for name in ("present_days", "hours_sum", "hours_count", "late_count", "total_late_minutes", "leave_count", "on_duty_count"):
        group[name] = {"$sum": statistic(name)}
    return [
        {"$match": filters}, monthly_logs(first, last), {"$group": group},
        {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1,
            "avg_work_hours": {"$cond": [{"$gt": ["$hours_count", 0]},
                mongo_half_up({"$divide": ["$hours_sum", "$hours_count"]}, 2), None]}}},
        {"$sort": {"department": 1}},
    ]


def late_leaderboard_pipeline(month: str, department: Optional[str], limit: int):
    first, last = month_bounds(month)
    pipeline = [
        {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        # Rank by late minutes alone. Employee code only decides display order within a tie.
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "emp_code": "$_id", "name": "$employee.name",
            "department": "$employee.department", "total_late_minutes": 1, "late_count": 1, "rank": 1}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ])
    return pipeline


def department_trend_pipeline(department: str, first: date, last: date):
    return [
        {"$match": {"department": department}}, {"$limit": 1},
        # Generate every requested day in MongoDB, including days with no logs at all.
        {"$project": {"_id": 0, "days": {"$range": [0, (last - first).days + 1]}}},
        {"$unwind": "$days"},
        {"$set": {"date": {"$dateToString": {"date": {"$dateAdd": {
            "startDate": calendar_day(first.isoformat()), "unit": "day", "amount": "$days"}}, "format": "%Y-%m-%d"}}}},
        {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [
            {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},
            {"$count": "count"}], "as": "people"}},
        {"$lookup": {"from": "attendance_logs", "localField": "date", "foreignField": "date", "pipeline": [
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code",
                "pipeline": [{"$match": {"department": department}}], "as": "employee"}},
            {"$match": {"employee.0": {"$exists": True}}},
        ] + log_statistics(), "as": "stats"}},
        {"$set": {"is_working_day": weekday("$date"),
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$people.count", 0]}, 0]},
            "present_count": statistic("present_count"), "late_count": statistic("late_count")}},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            mongo_half_up({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4), None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_average": {
            "$avg": {"$toDecimal": "$attendance_rate"}, "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1,
            "moving_avg_7d": mongo_half_up("$moving_average", 4)}},
        {"$sort": {"date": 1}},
    ]


def analytics_operation(endpoint: str, emp_code=None, month=None, department=None, limit=10, start=None, end=None):
    """Build the same pipeline and index choice for reports and explain requests."""
    if endpoint == "employee_monthly":
        if emp_code is None:
            query_error("emp_code", "field required")
        if month is None:
            query_error("month", "field required")
        return "employees", employee_monthly_pipeline(emp_code, month), "emp_code_1"
    if endpoint == "department_summary":
        if month is None:
            query_error("month", "field required")
        hint = "joined_on_1" if department is None else "department_1_joined_on_1"
        return "employees", department_summary_pipeline(month, department), hint
    if endpoint == "late_leaderboard":
        if month is None:
            query_error("month", "field required")
        return "attendance_logs", late_leaderboard_pipeline(month, department, limit), "date_1"
    if department is None:
        query_error("department", "field required")
    first, last = trend_bounds(start, end)
    return "employees", department_trend_pipeline(department, first, last), "department_1_joined_on_1"


def run_analytics(endpoint: str, **parameters):
    collection, pipeline, hint = analytics_operation(endpoint, **parameters)
    return list(db[collection].aggregate(pipeline, hint=hint))


@app.get("/analytics/employees/{emp_code}/monthly", responses={
    200: {"model": EmployeeMonthly}, 404: ERROR_RESPONSE, 422: VALIDATION_RESPONSE,
})
def employee_monthly(emp_code: str, month: str = Query(pattern=MONTH_PATTERN)):
    rows = run_analytics("employee_monthly", emp_code=emp_code, month=month)
    if not rows:
        raise HTTPException(404, "employee not found")
    return rows[0]


@app.get("/analytics/departments/summary", responses={200: {"model": DepartmentSummary}, 422: VALIDATION_RESPONSE})
def department_summary(month: str = Query(pattern=MONTH_PATTERN), department: Union[str, SkipJsonSchema[None]] = None):
    return {"month": month, "items": run_analytics("department_summary", month=month, department=department)}


@app.get("/analytics/leaderboard/late", responses={200: {"model": Leaderboard}, 422: VALIDATION_RESPONSE})
def late_leaderboard(month: str = Query(pattern=MONTH_PATTERN), limit: int = Query(default=10, ge=1, le=50),
                     department: Union[str, SkipJsonSchema[None]] = None):
    return {"month": month, "items": run_analytics("late_leaderboard", month=month, department=department, limit=limit)}


@app.get("/analytics/departments/{department}/trend", responses={
    200: {"model": Trend}, 404: ERROR_RESPONSE, 422: VALIDATION_RESPONSE,
})
def department_trend(department: str,
                     start: str = Query(alias="from", json_schema_extra={"format": "date"}),
                     end: str = Query(alias="to", json_schema_extra={"format": "date"})):
    operation = analytics_operation("department_trend", department=department, start=start, end=end)
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "department not found")
    collection, pipeline, hint = operation
    return {"department": department, "items": list(db[collection].aggregate(pipeline, hint=hint))}


@app.get("/admin/explain/{endpoint}", responses={200: {"model": ExplainResponse}, 422: VALIDATION_RESPONSE})
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Union[str, SkipJsonSchema[None]] = None,
    month: Union[str, SkipJsonSchema[None]] = Query(default=None, pattern=MONTH_PATTERN, json_schema_extra={"pattern": MONTH_PATTERN}),
    department: Union[str, SkipJsonSchema[None]] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Union[str, SkipJsonSchema[None]] = Query(default=None, json_schema_extra={"format": "date"}),
    date_to: Union[str, SkipJsonSchema[None]] = Query(default=None, json_schema_extra={"format": "date"}),
    status: Union[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"], SkipJsonSchema[None]] = None,
    start: Union[str, SkipJsonSchema[None]] = Query(default=None, alias="from", json_schema_extra={"format": "date"}),
    end: Union[str, SkipJsonSchema[None]] = Query(default=None, alias="to", json_schema_extra={"format": "date"}),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        operation = attendance_find_operation(emp_code, date_from, date_to, status, page, page_size)
        collection = "attendance_logs"
    else:
        collection, pipeline, hint = analytics_operation(endpoint, emp_code, month, department, limit, start, end)
        operation = {"aggregate": collection, "pipeline": pipeline, "cursor": {}, "hint": hint}
    explanation = db.command("explain", operation, verbosity="executionStats")
    # Preserve BSON values in the raw plan using MongoDB's Extended JSON representation.
    return {"endpoint": endpoint, "collection": collection, "explain": json.loads(json_util.dumps(explanation))}
