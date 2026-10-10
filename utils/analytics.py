"""MongoDB aggregation pipelines behind every dashboard, chart and course-analytics page.

Nothing here is hardcoded: every number comes from a query over the collections.
"""
from datetime import timedelta
from database.mongodb import get_db
from utils.helpers import course_stats_map, utcnow, users_map
PROGRESS_BUCKETS = [("0-25%", 0, 25), ("26-50%", 25, 50), ("51-75%", 50, 75), ("76-100%", 75, 101)]
def _bucket(values):
    return [sum(1 for v in values if lo <= v < hi) for _, lo, hi in PROGRESS_BUCKETS]
def course_analytics(course_id):
    db = get_db()
    total_students = db.enrollments.count_documents({"course_id": course_id})
    completed = db.enrollments.count_documents({"course_id": course_id, "status": "COMPLETED"})
    lessons_total = db.lessons.count_documents({"course_id": course_id})
    done_per_student = {r["_id"]: r["n"] for r in db.progress.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": "$student_id", "n": {"$sum": 1}}},
    ])}
    percents = []
    for enrollment in db.enrollments.find({"course_id": course_id}, {"student_id": 1}):
        done = done_per_student.get(enrollment["student_id"], 0)
        percents.append(done / lessons_total * 100 if lessons_total else 0)
    avg_progress = round(sum(percents) / len(percents), 1) if percents else 0
    titles = {q["_id"]: q["title"] for q in db.quizzes.find({"course_id": course_id}, {"title": 1})}
    quiz_rows = []
    for r in db.quiz_attempts.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": "$quiz_id", "attempts": {"$sum": 1}, "avg": {"$avg": "$percentage"},
                    "passed": {"$sum": {"$cond": ["$passed", 1, 0]}}}},
        {"$sort": {"attempts": -1}},
    ]):
        quiz_rows.append({"title": titles.get(r["_id"], "Deleted quiz"), "attempts": r["attempts"],
                          "avg": round(r["avg"] or 0, 1), "passed": r["passed"]})
    overall = list(db.quiz_attempts.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": None, "avg": {"$avg": "$percentage"}}},
    ]))
    avg_quiz = round(overall[0]["avg"], 1) if overall and overall[0]["avg"] is not None else 0
    a_titles = {a["_id"]: a["title"] for a in db.assignments.find({"course_id": course_id}, {"title": 1})}
    assignment_rows = []
    for r in db.assignment_submissions.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": "$assignment_id", "submitted": {"$sum": 1},
                    "evaluated": {"$sum": {"$cond": [{"$eq": ["$status", "evaluated"]}, 1, 0]}},
                    "late": {"$sum": {"$cond": [{"$eq": ["$status", "late"]}, 1, 0]}}}},
    ]):
        assignment_rows.append({"title": a_titles.get(r["_id"], "Deleted"), "submitted": r["submitted"],
                                "evaluated": r["evaluated"], "late": r["late"],
                                "pending": r["submitted"] - r["evaluated"]})
    pending = sum(row["pending"] for row in assignment_rows)
    rating = list(db.reviews.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
    ]))
    recent_attempts = list(db.quiz_attempts.find({"course_id": course_id})
                           .sort("attempted_at", -1).limit(10))
    return {
        "total_students": total_students,
        "completed": completed,
        "pending_students": total_students - completed,
        "avg_progress": avg_progress,
        "distribution": _bucket(percents),
        "avg_quiz": avg_quiz,
        "quiz_rows": quiz_rows,
        "assignment_rows": assignment_rows,
        "assignments_pending": pending,
        "rating": round(rating[0]["avg"], 1) if rating and rating[0]["avg"] is not None else 0,
        "reviews": rating[0]["count"] if rating else 0,
        "recent_attempts": recent_attempts,
    }
def course_student_rows(course_id):
    """Enrolled students with progress and quiz average, for the staff/admin student tables."""
    db = get_db()
    enrollments = list(db.enrollments.find({"course_id": course_id}).sort("enrolled_at", -1))
    students = users_map([e["student_id"] for e in enrollments])
    averages = {r["_id"]: round(r["avg"], 1) for r in db.quiz_attempts.aggregate([
        {"$match": {"course_id": course_id}},
        {"$group": {"_id": "$student_id", "avg": {"$avg": "$percentage"}}},
    ])}
    from utils.helpers import course_progress 
    rows = []
    for e in enrollments:
        student = students.get(e["student_id"])
        if not student:
            continue
        rows.append({
            "student": student, "enrollment": e,
            "progress": course_progress(e["student_id"], course_id),
            "quiz_avg": averages.get(e["student_id"]),
        })
    return rows
def admin_dashboard_data():
    db = get_db()
    cards = {
        "students": db.users.count_documents({"role": "student"}),
        "staff": db.users.count_documents({"role": "staff"}),
        "courses": db.courses.count_documents({}),
        "enrollments": db.enrollments.count_documents({}),
        "completed": db.enrollments.count_documents({"status": "COMPLETED"}),
        "certificates": db.certificates.count_documents({}),
        "reviews": db.reviews.count_documents({}),
    }
    avg = list(db.reviews.aggregate([{"$group": {"_id": None, "avg": {"$avg": "$rating"}}}]))
    cards["avg_rating"] = round(avg[0]["avg"], 1) if avg and avg[0]["avg"] is not None else 0

    courses = {c["_id"]: c for c in db.courses.find({}, {"title": 1, "category": 1, "instructor_id": 1})}
    now = utcnow()
    monthly = list(db.enrollments.aggregate([
        {"$match": {"enrolled_at": {"$gte": now - timedelta(days=365)}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$enrolled_at"}},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    per_course = list(db.enrollments.aggregate([
        {"$group": {"_id": "$course_id", "enrolled": {"$sum": 1},
                    "completed": {"$sum": {"$cond": [{"$eq": ["$status", "COMPLETED"]}, 1, 0]}}}},
        {"$sort": {"enrolled": -1}},
    ]))
    per_course = [r for r in per_course if r["_id"] in courses]
    wish = list(db.wishlists.aggregate([
        {"$group": {"_id": "$course_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 6},
    ]))
    wish = [w for w in wish if w["_id"] in courses]
    quiz_avg = list(db.quiz_attempts.aggregate([
        {"$group": {"_id": "$course_id", "avg": {"$avg": "$percentage"}}},
    ]))
    quiz_avg = [q for q in quiz_avg if q["_id"] in courses]
    activity = list(db.activity_logs.aggregate([
        {"$match": {"created_at": {"$gte": now - timedelta(days=7)}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    categories = list(db.courses.aggregate([
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))
    per_instructor = list(db.enrollments.aggregate([
        {"$group": {"_id": "$instructor_id", "students": {"$sum": 1}}},
        {"$sort": {"students": -1}},
    ]))
    instructors = users_map([r["_id"] for r in per_instructor])
    charts = {
        "monthly": {"labels": [r["_id"] for r in monthly], "values": [r["count"] for r in monthly]},
        "course_enrollment": {
            "labels": [courses[r["_id"]]["title"][:28] for r in per_course[:10]],
            "values": [r["enrolled"] for r in per_course[:10]]},
        "wishlist": {"labels": [courses[r["_id"]]["title"][:28] for r in wish],
                     "values": [r["count"] for r in wish]},
        "completion": {
            "labels": [courses[r["_id"]]["title"][:28] for r in per_course[:10]],
            "values": [round(r["completed"] / r["enrolled"] * 100, 1) if r["enrolled"] else 0
                       for r in per_course[:10]]},
        "quiz_scores": {"labels": [courses[r["_id"]]["title"][:28] for r in quiz_avg],
                        "values": [round(r["avg"], 1) for r in quiz_avg]},
        "activity": {"labels": [r["_id"] for r in activity], "values": [r["count"] for r in activity]},
        "categories": {"labels": [r["_id"] or "Uncategorized" for r in categories],
                       "values": [r["count"] for r in categories]},
        "instructors": {"labels": [instructors[r["_id"]]["name"] if r["_id"] in instructors else "Unassigned"
                                   for r in per_instructor],
                        "values": [r["students"] for r in per_instructor]},
    }
    return {"cards": cards, "charts": charts}
def staff_dashboard_data(instructor_id):
    db = get_db()
    courses = list(db.courses.find({"instructor_id": instructor_id}).sort("created_at", -1))
    analytics = {c["_id"]: course_analytics(c["_id"]) for c in courses}
    stats = course_stats_map()
    total_students = sum(a["total_students"] for a in analytics.values())
    with_students = [a["avg_progress"] for a in analytics.values() if a["total_students"]]
    quiz_scored = [a["avg_quiz"] for a in analytics.values() if a["recent_attempts"] or a["quiz_rows"]]
    distribution = [0, 0, 0, 0]
    for a in analytics.values():
        distribution = [x + y for x, y in zip(distribution, a["distribution"])]
    titles = {c["_id"]: c["title"][:28] for c in courses}
    return {
        "courses": courses,
        "stats": stats,
        "analytics": analytics,
        "cards": {
            "courses": len(courses),
            "students": total_students,
            "avg_progress": round(sum(with_students) / len(with_students), 1) if with_students else 0,
            "avg_quiz": round(sum(quiz_scored) / len(quiz_scored), 1) if quiz_scored else 0,
            "completed": sum(a["completed"] for a in analytics.values()),
            "pending_assignments": sum(a["assignments_pending"] for a in analytics.values()),
        },
        "charts": {
            "enrollment": {"labels": [titles[c["_id"]] for c in courses],
                           "values": [analytics[c["_id"]]["total_students"] for c in courses]},
            "progress": {"labels": [b[0] for b in PROGRESS_BUCKETS], "values": distribution},
            "quiz": {"labels": [titles[c["_id"]] for c in courses],
                     "values": [analytics[c["_id"]]["avg_quiz"] for c in courses]},
            "completion": {"labels": [titles[c["_id"]] for c in courses],
                           "values": [analytics[c["_id"]]["completed"] for c in courses]},
        },
    }
