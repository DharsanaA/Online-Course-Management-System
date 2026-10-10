"""CSV and PDF reports built from live MongoDB data. Admins see everything; staff see only their courses."""
from flask import Blueprint, abort, url_for
from database.mongodb import get_db
from utils.auth import current_user, login_required
from utils.documents import csv_response, pdf_response
from utils.helpers import course_progress, log_activity, oid, users_map, utcnow
from bson import ObjectId
bp = Blueprint("reports", __name__, url_prefix="/reports")
ADMIN_ONLY = {"students", "staff"}
REPORT_TITLES = {
    "students": "Student Report", "staff": "Staff Report", "courses": "Course Report",
    "enrollments": "Enrollment Report", "progress": "Progress Report", "quizzes": "Quiz Report",
    "assignments": "Assignment Report", "certificates": "Certificate Report", "reviews": "Review & Rating Report",
}
def _course_scope(user):
    """None means all courses (admin); otherwise the list of course ids the staff member teaches."""
    if user["role"] == "admin":
        return None
    return [c["_id"] for c in get_db().courses.find({"instructor_id": user["_id"]}, {"_id": 1})]
def _scoped(match, scope, key="course_id"):
    if scope is not None:
        match[key] = {"$in": scope}
    return match
def _rows(kind, user):
    db = get_db()
    scope = _course_scope(user)
    courses = {c["_id"]: c for c in db.courses.find(_scoped({}, scope, "_id"))}
    titles = {cid: c["title"] for cid, c in courses.items()}
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else ""  # noqa: E731
    if kind == "students":
        headers = ["Name", "Email", "Joined", "Active", "Enrollments", "Completed", "Certificates"]
        rows = []
        for s in db.users.find({"role": "student"}).sort("name", 1):
            rows.append([s["name"], s["email"], fmt(s.get("created_at")), "Yes" if s.get("is_active", True) else "No",
                         db.enrollments.count_documents({"student_id": s["_id"]}),
                         db.enrollments.count_documents({"student_id": s["_id"], "status": "COMPLETED"}),
                         db.certificates.count_documents({"student_id": s["_id"]})])
        return headers, rows
    if kind == "staff":
        headers = ["Name", "Email", "Expertise", "Courses", "Students", "Active"]
        rows = []
        for s in db.users.find({"role": "staff"}).sort("name", 1):
            rows.append([s["name"], s["email"], s.get("expertise", ""),
                         db.courses.count_documents({"instructor_id": s["_id"]}),
                         db.enrollments.count_documents({"instructor_id": s["_id"]}),
                         "Yes" if s.get("is_active", True) else "No"])
        return headers, rows
    if kind == "courses":
        headers = ["Title", "Category", "Difficulty", "Status", "Instructor", "Students", "Completed", "Rating"]
        instructors = users_map([c.get("instructor_id") for c in courses.values()])
        rows = []
        for c in courses.values():
            students = db.enrollments.count_documents({"course_id": c["_id"]})
            completed = db.enrollments.count_documents({"course_id": c["_id"], "status": "COMPLETED"})
            rating = list(db.reviews.aggregate([{"$match": {"course_id": c["_id"]}},
                                                {"$group": {"_id": None, "avg": {"$avg": "$rating"}}}]))
            inst = instructors.get(c.get("instructor_id"))
            rows.append([c["title"], c.get("category", ""), c.get("difficulty", ""), c.get("status", ""),
                         inst["name"] if inst else "Unassigned", students, completed,
                         round(rating[0]["avg"] or 0, 1) if rating else 0])
        return headers, rows
    if kind == "enrollments":
        headers = ["Student", "Email", "Course", "Enrolled On", "Status", "Completed On"]
        students = users_map([e["student_id"] for e in db.enrollments.find(_scoped({}, scope))])
        rows = []
        for e in db.enrollments.find(_scoped({}, scope)).sort("enrolled_at", -1):
            s = students.get(e["student_id"], {})
            rows.append([s.get("name", ""), s.get("email", ""), titles.get(e["course_id"], ""),
                         fmt(e.get("enrolled_at")), e.get("status", "ACTIVE"), fmt(e.get("completed_at"))])
        return headers, rows
    if kind == "progress":
        headers = ["Student", "Course", "Lessons Done", "Lessons Total", "Progress %", "Quizzes Passed",
                   "Assignments Submitted", "Status"]
        students = users_map([e["student_id"] for e in db.enrollments.find(_scoped({}, scope))])
        rows = []
        for e in db.enrollments.find(_scoped({}, scope)):
            p = course_progress(e["student_id"], e["course_id"])
            s = students.get(e["student_id"], {})
            rows.append([s.get("name", ""), titles.get(e["course_id"], ""), p["lessons_done"], p["lessons_total"],
                         p["percent"], f"{p['quizzes_passed']}/{p['quizzes_total']}",
                         f"{p['assignments_submitted']}/{p['assignments_total']}", p["status"]])
        return headers, rows
    if kind == "quizzes":
        headers = ["Student", "Course", "Quiz", "Attempt", "Score", "Total", "Percentage", "Result", "Attempted On"]
        rows = []
        for a in db.quiz_attempts.find(_scoped({}, scope)).sort("attempted_at", -1):
            quiz = db.quizzes.find_one({"_id": a["quiz_id"]}, {"title": 1})
            rows.append([a["student_name"], titles.get(a["course_id"], ""), quiz["title"] if quiz else "Deleted",
                         a.get("attempt_no", 1), a["score"], a["total_marks"], f"{a['percentage']}%",
                         "Passed" if a["passed"] else ("Timed out" if a.get("timed_out") else "Failed"),
                         fmt(a.get("attempted_at"))])
        return headers, rows
    if kind == "assignments":
        headers = ["Student", "Course", "Assignment", "Status", "Marks", "Max Marks", "Submitted On"]
        rows = []
        for s in db.assignment_submissions.find(_scoped({}, scope)).sort("submitted_at", -1):
            a = db.assignments.find_one({"_id": s["assignment_id"]}, {"title": 1, "max_marks": 1})
            rows.append([s["student_name"], titles.get(s["course_id"], ""), a["title"] if a else "Deleted",
                         s["status"].title(), s.get("marks", ""), a["max_marks"] if a else "",
                         fmt(s.get("submitted_at"))])
        return headers, rows
    if kind == "certificates":
        headers = ["Certificate ID", "Student", "Course", "Instructor", "Completion Date"]
        rows = []
        for c in db.certificates.find(_scoped({}, scope)).sort("issued_at", -1):
            rows.append([c["certificate_id"], c["student_name"], c["course_title"], c["instructor_name"],
                         fmt(c.get("completion_date"))])
        return headers, rows
    if kind == "reviews":
        headers = ["Course", "Student", "Rating", "Comment", "Date"]
        rows = []
        for r in db.reviews.find(_scoped({}, scope)).sort("created_at", -1):
            rows.append([titles.get(r["course_id"], ""), r["student_name"], r["rating"], r["comment"],
                         fmt(r.get("created_at"))])
        return headers, rows
    return None, None
@bp.route("/<kind>.<fmt>")
@login_required("admin", "staff")
def download(kind, fmt):
    user = current_user()
    if kind not in REPORT_TITLES or fmt not in ("csv", "pdf"):
        abort(404)
    if kind in ADMIN_ONLY and user["role"] != "admin":
        abort(403)
    headers, rows = _rows(kind, user)
    log_activity(user["_id"], user["role"], "Report downloaded", f"{kind}.{fmt}")
    filename = f"{kind}_report_{utcnow():%Y%m%d}"
    if fmt == "csv":
        return csv_response(filename, headers, rows)
    return pdf_response(filename, REPORT_TITLES[kind], headers, rows)
