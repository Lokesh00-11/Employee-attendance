"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
import pymongo
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

# Create indexes idempotently at startup
db.employees.create_index("emp_code", unique=True)
db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in_ist: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in_ist - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    # Rule 4: rounded to 2 decimals half-up
    from decimal import Decimal, ROUND_HALF_UP
    hours = (punch_out - punch_in).total_seconds() / 3600
    return float(Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out_ist: datetime, shift_end: str, date_str: str, shift_start: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=IST)
    if shift_end <= shift_start:
        end += timedelta(days=1)
    minutes = int((punch_out_ist - end).total_seconds() / 60)
    return minutes if minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")
    doc = body.model_dump()
    
    now = datetime.now(timezone.utc)
    doc["created_at"] = now.replace(tzinfo=None) # Store as naive BSON date
    
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    
    doc.pop("_id", None)
    doc["created_at"] = int(now.timestamp() * 1000)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    
    for item in items:
        if "created_at" in item:
            item["created_at"] = int(item["created_at"].replace(tzinfo=timezone.utc).timestamp() * 1000)
            
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    
    # R1: Truncate to whole seconds
    ts = ts.replace(microsecond=0)
    ts_ist = ts.astimezone(IST)
    
    d = ts_ist.date()
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    
    # Overnight shift check
    if shift_end <= shift_start:
        end_time = datetime.strptime(shift_end, "%H:%M").time()
        if ts_ist.time() < end_time:
            d -= timedelta(days=1)
            
    date_str = d.isoformat()
        
    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts.replace(tzinfo=None),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts_ist, shift_start),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    doc.pop("_id", None)
    
    doc["punch_in"] = int(ts.timestamp() * 1000)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from > date_to")
        
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    
    page_docs = list(db.attendance_logs.find(q, {"_id": 0}).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size))
    
    for d in page_docs:
        if d.get("punch_in"):
            d["punch_in"] = int(d["punch_in"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        if d.get("punch_out"):
            d["punch_out"] = int(d["punch_out"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        for h in d.get("history", []):
            h["at"] = int(h["at"].replace(tzinfo=timezone.utc).timestamp() * 1000)
            for k in ["punch_in", "punch_out"]:
                if k in h["changes"]:
                    if h["changes"][k]["from"]:
                        h["changes"][k]["from"] = int(h["changes"][k]["from"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                    if h["changes"][k]["to"]:
                        h["changes"][k]["to"] = int(h["changes"][k]["to"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                        
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Analytics and Admin Endpoints
# --------------------------------------------------------------------------- #

def get_working_days(joined_on: str, month: str) -> int:
    y, m = map(int, month.split("-"))
    _, days_in_month = calendar.monthrange(y, m)
    working_days = 0
    for day in range(1, days_in_month + 1):
        dt_str = f"{y:04d}-{m:02d}-{day:02d}"
        if dt_str >= joined_on:
            dt = datetime(y, m, day)
            if dt.weekday() < 5:  # Mon-Fri
                working_days += 1
    return working_days

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., regex=r"^\d{4}-(0[1-9]|1[0-2])$")):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    working_days = get_working_days(emp["joined_on"], month)
    
    start_date = f"{month}-01"
    y, m = map(int, month.split("-"))
    end_date = f"{y + (m // 12):04d}-{(m % 12) + 1:02d}-01"

    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start_date, "$lt": end_date}
        }},
        {"$addFields": {
            "parsed_date": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}
        }},
        {"$addFields": {
            "day_of_week": {"$dayOfWeek": "$parsed_date"}
        }},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]}
                        ]},
                        {"$cond": ["$half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"}
        }}
    ]

    res = list(db.attendance_logs.aggregate(pipeline))
    agg = res[0] if res else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0
    }
    
    from decimal import Decimal, ROUND_HALF_UP
    pct = None
    if working_days > 0:
        val = (agg["present_days"] / working_days) * 100
        pct = float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": agg["present_days"],
        "leave_days": agg["leave_days"],
        "late_count": agg["late_count"],
        "total_late_minutes": agg["total_late_minutes"],
        "total_overtime_minutes": agg["total_overtime_minutes"],
        "attendance_pct": pct
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., regex=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None):
    y, m = map(int, month.split("-"))
    _, days_in_month = calendar.monthrange(y, m)
    start_date = f"{month}-01"
    end_date = f"{y + (m // 12):04d}-{(m % 12) + 1:02d}-01"
    month_end = f"{month}-{days_in_month:02d}"

    emp_match = {"joined_on": {"$lte": month_end}}
    if department:
        emp_match["department"] = department
        
    pipeline = [
        {"$match": emp_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$code"]},
                    "date": {"$gte": start_date, "$lt": end_date}
                }},
                {"$addFields": {
                    "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}
                }}
            ],
            "as": "logs"
        }},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "all_logs": {"$push": "$logs"}
        }},
        {"$project": {
            "department": "$_id",
            "_id": 0,
            "headcount": 1,
            "flat_logs": {"$reduce": {
                "input": "$all_logs",
                "initialValue": [],
                "in": {"$concatArrays": ["$$value", "$$this"]}
            }}
        }},
        {"$unwind": {
            "path": "$flat_logs",
            "preserveNullAndEmptyArrays": True
        }},
        {"$group": {
            "_id": {
                "dept": "$department",
                "hc": "$headcount"
            },
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$flat_logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": ["$flat_logs.day_of_week", [2, 3, 4, 5, 6]]}
                        ]},
                        {"$cond": ["$flat_logs.half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$flat_logs.late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {"$sum": {"$ifNull": ["$flat_logs.late_minutes", 0]}},
            "leave_count": {
                "$sum": {"$cond": [{"$eq": ["$flat_logs.status", "LEAVE"]}, 1, 0]}
            },
            "on_duty_count": {
                "$sum": {"$cond": [{"$eq": ["$flat_logs.status", "ON_DUTY"]}, 1, 0]}
            },
            "work_hours_arr": {
                "$push": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$flat_logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$ne": ["$flat_logs.work_hours", None]}
                        ]},
                        "$flat_logs.work_hours",
                        "$$REMOVE"
                    ]
                }
            }
        }},
        {"$sort": {"_id.dept": 1}}
    ]
    
    results = list(db.employees.aggregate(pipeline))
    
    from decimal import Decimal, ROUND_HALF_UP
    items = []
    for r in results:
        dept = r["_id"]["dept"]
        hc = r["_id"]["hc"]
        arr = r.get("work_hours_arr", [])
        avg = None
        if arr:
            avg_val = sum(arr) / len(arr)
            avg = float(Decimal(str(avg_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            
        items.append({
            "department": dept,
            "headcount": hc,
            "present_days": r["present_days"],
            "avg_work_hours": avg,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"]
        })
        
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., regex=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = 10,
    department: Optional[str] = None
):
    start_date = f"{month}-01"
    y, m = map(int, month.split("-"))
    end_date = f"{y + (m // 12):04d}-{(m % 12) + 1:02d}-01"
    
    pipeline = [
        {"$match": {"date": {"$gte": start_date, "$lt": end_date}, "late_minutes": {"$gt": 0}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"}
    ]
    
    if department:
        pipeline.append({"$match": {"emp.department": department}})
        
    pipeline.extend([
        {"$project": {
            "emp_code": "$_id",
            "_id": 0,
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1
        }},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])
    
    docs = list(db.attendance_logs.aggregate(pipeline))
    
    # We calculate the standard competition ranking in python since skip ranks can be tricky in mongo
    items = []
    current_rank = 1
    for i, d in enumerate(docs):
        if i > 0 and d["total_late_minutes"] < docs[i-1]["total_late_minutes"]:
            current_rank = i + 1
        if current_rank > limit:
            break
        d["rank"] = current_rank
        items.append({
            "rank": d["rank"],
            "emp_code": d["emp_code"],
            "name": d["name"],
            "department": d["department"],
            "total_late_minutes": d["total_late_minutes"],
            "late_count": d["late_count"]
        })
        
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    if from_date > to_date:
        raise HTTPException(422, "from > to")
        
    fd = datetime.fromisoformat(from_date)
    td = datetime.fromisoformat(to_date)
    if (td - fd).days > 92:
        raise HTTPException(422, "range exceeds 92 days")
        
    emp_count = db.employees.count_documents({"department": department})
    if emp_count == 0:
        raise HTTPException(404, "department not found")
        
    pipeline = [
        {"$match": {
            "date": {"$gte": from_date, "$lte": to_date}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"},
        {"$match": {"emp.department": department}},
        {"$group": {
            "_id": "$date",
            "present_count": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": ["$half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            }
        }},
        {"$project": {
            "date": {"$dateFromString": {"dateString": "$_id"}},
            "present_count": 1,
            "late_count": 1
        }},
        {"$sort": {"date": 1}},
        {"$densify": {
            "field": "date",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [fd, td + timedelta(days=1)]
            }
        }},
        {"$project": {
            "date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
            "day_of_week": {"$dayOfWeek": "$date"}
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"d": "$date_str"},
            "pipeline": [
                {"$match": {
                    "department": department,
                    "$expr": {"$lte": ["$joined_on", "$$d"]}
                }},
                {"$count": "hc"}
            ],
            "as": "hc_docs"
        }},
        {"$addFields": {
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$hc_docs.hc", 0]}, 0]}
        }},
        {"$sort": {"date_str": 1}}
    ]

    docs = list(db.attendance_logs.aggregate(pipeline))
    
    from decimal import Decimal, ROUND_HALF_UP
    items = []
    rates = []
    
    for d in docs:
        is_working_day = d["day_of_week"] in [2, 3, 4, 5, 6]
        rate = None
        if is_working_day and d["headcount"] > 0:
            rate_val = d["present_count"] / d["headcount"]
            rate = float(Decimal(str(rate_val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            
        rates.append(rate)
        
        # Calculate moving average from the 7-day window
        window = [r for r in rates[-7:] if r is not None]
        moving_avg = None
        if window:
            avg_val = sum(window) / len(window)
            moving_avg = float(Decimal(str(avg_val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            
        items.append({
            "date": d["date_str"],
            "is_working_day": is_working_day,
            "headcount": d["headcount"],
            "present_count": d["present_count"],
            "late_count": d["late_count"],
            "attendance_rate": rate,
            "moving_avg_7d": moving_avg
        })
        
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    page_size: int = 20
):
    # This executes explain on the main queries defined in our analytics/endpoints above.
    
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        skip = (page - 1) * page_size
        query = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": query.explain()}
        
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422)
        start_date = f"{month}-01"
        y, m = map(int, month.split("-"))
        end_date = f"{y + (m // 12):04d}-{(m % 12) + 1:02d}-01"
        pipeline = [{"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lt": end_date}}}]
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")}
        
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422)
        y, m = map(int, month.split("-"))
        _, days_in_month = calendar.monthrange(y, m)
        month_end = f"{month}-{days_in_month:02d}"
        emp_match = {"joined_on": {"$lte": month_end}}
        if department: emp_match["department"] = department
        pipeline = [{"$match": emp_match}]
        return {"endpoint": endpoint, "collection": "employees", "explain": db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")}
        
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422)
        start_date = f"{month}-01"
        y, m = map(int, month.split("-"))
        end_date = f"{y + (m // 12):04d}-{(m % 12) + 1:02d}-01"
        pipeline = [{"$match": {"date": {"$gte": start_date, "$lt": end_date}, "late_minutes": {"$gt": 0}}}]
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")}
        
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date: raise HTTPException(422)
        pipeline = [{"$match": {"date": {"$gte": from_date, "$lte": to_date}}}]
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")}

    raise HTTPException(422, "Unknown endpoint")

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    
    ts = ts.replace(microsecond=0)
    ts_naive = ts.replace(tzinfo=None)
    
    # Find most recent record with punch_in <= punched_at
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts_naive}},
        sort=[("punch_in", -1)]
    )
    
    if not record:
        raise HTTPException(404, "no punch-in found")
        
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
        
    punch_in_naive = record["punch_in"]
    if ts_naive <= punch_in_naive:
        raise HTTPException(422, "punched_at must be after punch_in")
    if (ts_naive - punch_in_naive).total_seconds() > 24 * 3600:
        raise HTTPException(422, "punched_at cannot be more than 24 hours after punch_in")
        
    ts_ist = ts.astimezone(IST)
    
    work_hours = compute_work_hours(punch_in_naive, ts_naive)
    half_day = work_hours < 4.50
    overtime_minutes = compute_overtime(ts_ist, emp["shift_end"], record["date"], emp["shift_start"])
    
    updates = {
        "punch_out": ts_naive,
        "work_hours": work_hours,
        "half_day": half_day,
        "overtime_minutes": overtime_minutes
    }
    
    # find_one_and_update prevents concurrent punch-outs on the same record
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": updates},
        return_document=True
    )
    
    if not updated:
        raise HTTPException(409, "already punched out")
        
    updated.pop("_id", None)
    if updated.get("punch_in"):
        updated["punch_in"] = int(updated["punch_in"].replace(tzinfo=timezone.utc).timestamp() * 1000)
    if updated.get("punch_out"):
        updated["punch_out"] = int(updated["punch_out"].replace(tzinfo=timezone.utc).timestamp() * 1000)
    
    # Format history correctly
    for h in updated.get("history", []):
        h["at"] = int(h["at"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        for k in ["punch_in", "punch_out"]:
            if k in h["changes"]:
                if h["changes"][k]["from"]:
                    h["changes"][k]["from"] = int(h["changes"][k]["from"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                if h["changes"][k]["to"]:
                    h["changes"][k]["to"] = int(h["changes"][k]["to"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                    
    return updated


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
        
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "record not found")
        
    new_status = body.status if body.status is not None else record.get("status")
    
    def parse_time(epoch_ms):
        if epoch_ms is None: return None
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(microsecond=0, tzinfo=None)
        
    new_punch_in = parse_time(body.punch_in) if body.punch_in is not None else record.get("punch_in")
    new_punch_out = parse_time(body.punch_out) if body.punch_out is not None else record.get("punch_out")

    if new_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "cannot supply punch times for ABSENT/LEAVE")
        new_punch_in, new_punch_out = None, None
    else:
        if new_punch_in is None:
            raise HTTPException(422, "presence status requires punch_in")
            
        pi_ist = new_punch_in.replace(tzinfo=timezone.utc).astimezone(IST)
        d = pi_ist.date()
        if emp["shift_end"] <= emp["shift_start"]:
            end_time = datetime.strptime(emp["shift_end"], "%H:%M").time()
            if pi_ist.time() < end_time:
                d -= timedelta(days=1)
        if d.isoformat() != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
            
        if new_punch_out is not None:
            if new_punch_out <= new_punch_in:
                raise HTTPException(422, "punch_out must be after punch_in")
            if (new_punch_out - new_punch_in).total_seconds() > 24 * 3600:
                raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    new_late, new_work_hours, new_overtime, new_half_day = 0, None, 0, False
    
    if new_status not in ["ABSENT", "LEAVE"] and new_punch_in:
        pi_ist = new_punch_in.replace(tzinfo=timezone.utc).astimezone(IST)
        new_late = compute_late_minutes(pi_ist, emp["shift_start"])
        if new_punch_out:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_half_day = new_work_hours < 4.50
            po_ist = new_punch_out.replace(tzinfo=timezone.utc).astimezone(IST)
            new_overtime = compute_overtime(po_ist, emp["shift_end"], date, emp["shift_start"])

    final_changes = {}
    
    def check_change(field, old_val, new_val):
        if old_val != new_val:
            final_changes[field] = {"from": old_val, "to": new_val}
            return new_val
        return old_val

    set_updates = {
        "status": check_change("status", record.get("status"), new_status),
        "punch_in": check_change("punch_in", record.get("punch_in"), new_punch_in),
        "punch_out": check_change("punch_out", record.get("punch_out"), new_punch_out),
        "work_hours": check_change("work_hours", record.get("work_hours"), new_work_hours),
        "late_minutes": check_change("late_minutes", record.get("late_minutes", 0), new_late),
        "overtime_minutes": check_change("overtime_minutes", record.get("overtime_minutes", 0), new_overtime),
        "half_day": check_change("half_day", record.get("half_day", False), new_half_day)
    }
    
    if not final_changes:
        raise HTTPException(422, "request changes nothing")
        
    history_entry = {
        "at": datetime.now(timezone.utc).replace(tzinfo=None),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": final_changes
    }
    
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"]},
        {"$set": set_updates, "$push": {"history": history_entry}},
        return_document=True
    )
    
    updated.pop("_id", None)
    for field in ["punch_in", "punch_out"]:
        if updated.get(field):
            updated[field] = int(updated[field].replace(tzinfo=timezone.utc).timestamp() * 1000)
            
    for h in updated.get("history", []):
        h["at"] = int(h["at"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        for k in ["punch_in", "punch_out"]:
            if k in h["changes"]:
                if h["changes"][k]["from"]:
                    h["changes"][k]["from"] = int(h["changes"][k]["from"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                if h["changes"][k]["to"]:
                    h["changes"][k]["to"] = int(h["changes"][k]["to"].replace(tzinfo=timezone.utc).timestamp() * 1000)
                    
    return updated
