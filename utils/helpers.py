"""Shared helpers: time handling, validation, access rules, progress and notifications."""
import math
import re
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from bson.errors import InvalidId
from flask import abort, url_for
from database.mongodb import get_db
IST_OFFSET = timedelta(hours=5, minutes=30)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CERT_ID_RE = re.compile(r"^OCMS-\d{4}-\d{5}$")
DEFAULT_SETTINGS = {
    "_id": "platform",
    "name": "OCMS Learning",
    "description": "Learn new skills from expert instructors, at your own pace.",
    "contact_email": "support@example.com",
    "certificate_org": "OCMS Learning Platform",
    "passing_percent": 60,
}
def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
def to_local(value):
    return value + IST_OFFSET if value else None
def from_local(text):
    """Convert an <input type=datetime-local> value (IST) to naive UTC. Returns None if empty."""
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M") - IST_OFFSET
def fmt_dt(value, fmt="%d %b %Y, %I:%M %p"):
    if not value:
        return "—"
    return to_local(value).strftime(fmt)
def fmt_date(value):
    return fmt_dt(value, "%d %b %Y")
def local_input(value):
    """Format a stored UTC datetime for an <input type=datetime-local> in IST."""
    return to_local(value).strftime("%Y-%m-%dT%H:%M") if value else ""
def is_valid_oid(value):
    return bool(value) and ObjectId.is_valid(value)
def oid(value):
    """Convert a URL id to ObjectId, returning 404 for malformed ids."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        abort(404)
def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number
def paginate(total, page, per_page):
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    return {"page": page, "pages": pages, "total": total, "per_page": per_page,
            "skip": (page - 1) * per_page}
def validate_password(password):
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    return None
def get_settings():
    db = get_db()
    stored = db.settings.find_one({"_id": "platform"}) or {}
    return {**DEFAULT_SETTINGS, **stored}
def log_activity(user_id, role, action, details=""):
    get_db().activity_logs.insert_one({
        "user_id": user_id, "role": role, "action": action,
        "details": details, "created_at": utcnow(),
    })
def notify(user_id, title, message, link=None, kind="info"):
    get_db().notifications.insert_one({
        "user_id": user_id, "title": title, "message": message,
        "link": link, "kind": kind, "is_read": False, "created_at": utcnow(),
    })
def notify_many(user_ids, title, message, link=None, kind="info"):
    docs = [{"user_id": uid, "title": title, "message": message, "link": link,
             "kind": kind, "is_read": False, "created_at": utcnow()} for uid in user_ids]
    if docs:
        get_db().notifications.insert_many(docs)
def unread_count(user):
    if not user:
        return 0
    return get_db().notifications.count_documents({"user_id": user["_id"], "is_read": False})
def can_manage_course(user, course):
    if not user or not course:
        return False
    if user["role"] == "admin":
        return True
    return user["role"] == "staff" and course.get("instructor_id") == user["_id"]
def course_access(course_id, user):
    """Return a course the user may view. Drafts/archived courses are visible only to managers."""
    course = get_db().courses.find_one({"_id": oid(course_id)})
    if not course:
        abort(404)
    if course.get("status") != "published" and not can_manage_course(user, course):
        abort(404)
    return course
def manageable_course(course_id, user):
    """Return a course the user may manage, else 403 (exists but not yours) / 404."""
    course = get_db().courses.find_one({"_id": oid(course_id)})
    if not course:
        abort(404)
    if not can_manage_course(user, course):
        abort(403)
    return course
def is_enrolled(user_id, course_id):
    return get_db().enrollments.find_one({"student_id": user_id, "course_id": course_id}) is not None
def course_progress(student_id, course_id):
    """Progress for one student in one course.
    Lesson progress = completed lessons / total lessons x 100.
    Status is COMPLETED only when lessons, required quizzes (passed) and assignments (submitted) are all done.
    """
    db = get_db()
    lessons_total = db.lessons.count_documents({"course_id": course_id})
    lessons_done = db.progress.count_documents({"student_id": student_id, "course_id": course_id})

    quiz_ids = [q["_id"] for q in db.quizzes.find({"course_id": course_id, "published": True}, {"_id": 1})]
    passed_ids = set(db.quiz_attempts.distinct(
        "quiz_id", {"student_id": student_id, "course_id": course_id, "passed": True}))
    quizzes_total = len(quiz_ids)
    quizzes_passed = sum(1 for qid in quiz_ids if qid in passed_ids)

    assignment_ids = [a["_id"] for a in db.assignments.find({"course_id": course_id}, {"_id": 1})]
    submitted_ids = set(db.assignment_submissions.distinct(
        "assignment_id", {"student_id": student_id, "course_id": course_id}))
    assignments_total = len(assignment_ids)
    assignments_submitted = sum(1 for aid in assignment_ids if aid in submitted_ids)

    percent = round(lessons_done / lessons_total * 100, 1) if lessons_total else 0.0
    all_done = (lessons_total > 0 and lessons_done == lessons_total
                and quizzes_passed == quizzes_total and assignments_submitted == assignments_total)
    if all_done:
        status = "COMPLETED"
    elif lessons_done or quizzes_passed or assignments_submitted:
        status = "IN_PROGRESS"
    else:
        status = "NOT_STARTED"

    return {
        "lessons_total": lessons_total, "lessons_done": lessons_done,
        "lessons_remaining": lessons_total - lessons_done,
        "quizzes_total": quizzes_total, "quizzes_passed": quizzes_passed,
        "assignments_total": assignments_total, "assignments_submitted": assignments_submitted,
        "percent": percent, "status": status,
    }


# ---------- aggregation helpers ----------
def course_stats_map(course_id=None):
    """Aggregated enrollment count and rating per course (optionally for one course)."""
    db = get_db()
    match = {"course_id": course_id} if course_id else {}
    enrol = db.enrollments.aggregate([
        {"$match": match},
        {"$group": {"_id": "$course_id", "students": {"$sum": 1}}},
    ])
    ratings = db.reviews.aggregate([
        {"$match": match},
        {"$group": {"_id": "$course_id", "rating": {"$avg": "$rating"}, "reviews": {"$sum": 1}}},
    ])
    result = {}
    for row in enrol:
        result.setdefault(row["_id"], {})["students"] = row["students"]
    for row in ratings:
        entry = result.setdefault(row["_id"], {})
        entry["rating"] = round(row["rating"], 1)
        entry["reviews"] = row["reviews"]
    for entry in result.values():
        entry.setdefault("students", 0)
        entry.setdefault("rating", 0)
        entry.setdefault("reviews", 0)
    return result


def users_map(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {u["_id"]: u for u in get_db().users.find(
        {"_id": {"$in": ids}}, {"password_hash": 0})}


def upcoming_deadlines(course_ids, now, role, limit=6):
    """Quizzes and assignments due in the future, sorted soonest first."""
    db = get_db()
    titles = {c["_id"]: c["title"] for c in db.courses.find({"_id": {"$in": course_ids}}, {"title": 1})}
    items = []
    for q in db.quizzes.find({"course_id": {"$in": course_ids}, "published": True,
                              "deadline": {"$gte": now}}):
        items.append({"kind": "Quiz", "title": q["title"], "course": titles.get(q["course_id"], ""),
                      "due": q["deadline"],
                      "link": url_for("student.quiz_take", quiz_id=q["_id"]) if role == "student"
                      else url_for("staff.course_manage", course_id=q["course_id"])})
    for a in db.assignments.find({"course_id": {"$in": course_ids}, "due_date": {"$gte": now}}):
        items.append({"kind": "Assignment", "title": a["title"], "course": titles.get(a["course_id"], ""),
                      "due": a["due_date"],
                      "link": url_for("student.assignments") if role == "student"
                      else url_for("staff.course_manage", course_id=a["course_id"])})
    items.sort(key=lambda i: i["due"])
    return items[:limit]
