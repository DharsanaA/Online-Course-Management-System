"""Public website (home, catalogue, about), course details, enrollment, reviews, wishlist, instructor profiles."""

from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError

from config import Config
from database.mongodb import get_db
from utils.auth import current_user, login_required
from database.queries import (DURATION_BANDS, DURATION_LABELS, SORT_LABELS, SORT_SPEC, catalog_count,
                              catalog_filter_options, catalog_page, to_object_id)
from utils.helpers import (can_manage_course, course_access, course_progress, course_stats_map,
                           is_enrolled, is_valid_oid, log_activity, notify, oid, paginate,
                           safe_int, users_map, utcnow)

bp = Blueprint("courses", __name__)

DIFFICULTIES = ("Beginner", "Intermediate", "Advanced")


@bp.route("/")
def home():
    db = get_db()
    published = {"status": "published"}
    stats = {
        "courses": db.courses.count_documents(published),
        "students": db.users.count_documents({"role": "student"}),
        "staff": db.users.count_documents({"role": "staff"}),
        "enrollments": db.enrollments.count_documents({}),
        "certificates": db.certificates.count_documents({}),
    }
    cstats = course_stats_map()
    candidates = list(db.courses.find(published))
    candidates.sort(key=lambda c: (cstats.get(c["_id"], {}).get("students", 0), c["created_at"]), reverse=True)
    featured = candidates[:4]
    instructors = users_map([c.get("instructor_id") for c in featured])
    return render_template("home.html", stats=stats, featured=featured,
                           instructors=instructors, cstats=cstats)


@bp.route("/courses")
def catalog():
    """Course catalogue. Search, filters, sorting and pagination all run as MongoDB queries
    (see database/queries.py): $match, $lookup, $addFields, $count, $sort, $skip, $limit, $project."""
    db = get_db()
    args = request.args
    min_rating = {"3": 3, "4": 4, "4.5": 4.5}.get(args.get("min_rating", ""))
    filters = {
        "q": args.get("q", "").strip()[:100],
        "category": args.get("category", "").strip()[:60],
        "difficulty": args.get("difficulty", "") if args.get("difficulty") in DIFFICULTIES else "",
        "instructor_id": to_object_id(args.get("instructor", "")) if is_valid_oid(args.get("instructor", "")) else None,
        "duration": args.get("duration", "") if args.get("duration") in DURATION_BANDS else "",
        "min_rating": min_rating,
        "tag": args.get("tag", "").strip().lower()[:30],
    }
    sort = args.get("sort", "newest")
    if sort not in SORT_SPEC:
        sort = "newest"

    total = catalog_count(db, filters)                       # $count
    pg = paginate(total, safe_int(args.get("page"), 1, 1), Config.PER_PAGE)
    courses = catalog_page(db, filters, sort, pg["skip"], pg["per_page"])  # $sort/$skip/$limit/$project

    cstats = {c["_id"]: {"students": c["students"], "rating": round(c["rating"], 1),
                         "reviews": c["review_count"]} for c in courses}
    instructors = {c["instructor_id"]: {"name": c.get("instructor_name") or "Unknown"} for c in courses}
    options = catalog_filter_options(db)
    instructor_options = sorted(users_map(options["instructor_ids"]).values(), key=lambda u: u["name"])

    return render_template("courses.html", courses=courses, pg=pg, q=filters["q"],
                           category=filters["category"], cstats=cstats, difficulty=filters["difficulty"],
                           instructor=args.get("instructor", "") if filters["instructor_id"] else "",
                           duration=filters["duration"], duration_labels=DURATION_LABELS,
                           min_rating=args.get("min_rating", ""), tag=filters["tag"],
                           sort=sort, sort_options=SORT_LABELS, difficulties=DIFFICULTIES,
                           categories=options["categories"], instructor_options=instructor_options,
                           instructors=instructors)


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/courses/<course_id>")
def detail(course_id):
    db = get_db()
    user = current_user()
    course = course_access(course_id, user)
    cid = course["_id"]
    manager = can_manage_course(user, course)

    instructor = db.users.find_one({"_id": course["instructor_id"]}, {"password_hash": 0}) \
        if course.get("instructor_id") else None
    modules = list(db.modules.find({"course_id": cid}).sort("order", 1))
    lessons = list(db.lessons.find({"course_id": cid}).sort("order", 1))
    lessons_by_module = {}
    for lesson in lessons:
        lessons_by_module.setdefault(lesson.get("module_id"), []).append(lesson)
    quiz_filter = {"course_id": cid} if manager else {"course_id": cid, "published": True}
    quizzes = list(db.quizzes.find(quiz_filter, {"questions": 0}).sort("created_at", 1))
    assignments = list(db.assignments.find({"course_id": cid}).sort("due_date", 1))
    notes = list(db.materials.find({"course_id": cid, "lesson_id": None}))
    announcements = list(db.announcements.find({"$or": [{"course_id": cid}, {"course_id": None}]})
                         .sort("created_at", -1).limit(5))
    reviews = list(db.reviews.find({"course_id": cid}).sort("created_at", -1).limit(20))
    stats = course_stats_map(cid).get(cid, {"students": 0, "rating": 0, "reviews": 0})

    student = user if user and user["role"] == "student" else None
    enrolled = bool(student and is_enrolled(student["_id"], cid))
    in_wishlist = bool(student and db.wishlists.find_one({"student_id": student["_id"], "course_id": cid}))
    my_review = db.reviews.find_one({"course_id": cid, "student_id": student["_id"]}) if student else None
    progress = course_progress(student["_id"], cid) if enrolled else None

    return render_template(
        "course_detail.html", course=course, instructor=instructor, modules=modules,
        lessons_by_module=lessons_by_module, quizzes=quizzes, assignments=assignments,
        notes=notes, announcements=announcements, reviews=reviews, stats=stats,
        enrolled=enrolled, in_wishlist=in_wishlist, my_review=my_review,
        progress=progress, manager=manager, reviewers=users_map([r["student_id"] for r in reviews]),
        first_lesson=lessons[0] if lessons else None,
    )


@bp.route("/courses/<course_id>/enroll", methods=["POST"])
@login_required("student")
def enroll(course_id):
    db = get_db()
    user = current_user()
    course = course_access(course_id, user)
    if course.get("status") != "published":
        abort(404)
    if is_enrolled(user["_id"], course["_id"]):
        flash("You are already enrolled in this course.", "info")
        return redirect(url_for("courses.detail", course_id=course["_id"]))
    try:
        db.enrollments.insert_one({
            "student_id": user["_id"], "course_id": course["_id"],
            "instructor_id": course.get("instructor_id"), "status": "ACTIVE",
            "enrolled_at": utcnow(), "last_lesson_id": None, "last_accessed": None,
        })
    except DuplicateKeyError:  # unique index guards against double-clicks
        flash("You are already enrolled in this course.", "info")
        return redirect(url_for("courses.detail", course_id=course["_id"]))
    detail_link = url_for("courses.detail", course_id=course["_id"])
    notify(user["_id"], "Enrollment successful", f"You are now enrolled in {course['title']}.",
           link=url_for("student.my_learning"), kind="success")
    if course.get("instructor_id"):
        notify(course["instructor_id"], "New enrollment",
               f"{user['name']} enrolled in {course['title']}.", link=detail_link)
    log_activity(user["_id"], "student", "Enrollment", course["title"])
    flash("Enrollment successful! Start learning now.", "success")
    return redirect(url_for("student.my_learning"))
@bp.route("/courses/<course_id>/unenroll", methods=["POST"])
@login_required("student")
def unenroll(course_id):
    db = get_db()
    user = current_user()
    cid = oid(course_id)
    enrollment = db.enrollments.find_one({"student_id": user["_id"], "course_id": cid})
    if not enrollment:
        abort(404)
    progress = course_progress(user["_id"], cid)
    if progress["lessons_done"] > 0 or db.certificates.find_one({"student_id": user["_id"], "course_id": cid}):
        flash("You cannot cancel after starting lessons or earning a certificate. Contact your instructor.",
              "warning")
        return redirect(url_for("student.my_learning"))
    db.enrollments.delete_one({"_id": enrollment["_id"]})
    log_activity(user["_id"], "student", "Enrollment cancelled", str(cid))
    flash("Your enrollment has been cancelled.", "info")
    return redirect(url_for("courses.detail", course_id=cid))
@bp.route("/courses/<course_id>/review", methods=["POST"])
@login_required("student")
def review(course_id):
    db = get_db()
    user = current_user()
    course = course_access(course_id, user)
    if not is_enrolled(user["_id"], course["_id"]):
        flash("Only enrolled students can review a course.", "warning")
        return redirect(url_for("courses.detail", course_id=course["_id"]))
    rating = safe_int(request.form.get("rating"), 0)
    comment = request.form.get("comment", "").strip()
    if rating not in (1, 2, 3, 4, 5):
        flash("Choose a rating from 1 to 5 stars.", "danger")
    elif not 10 <= len(comment) <= 1000:
        flash("Your review must be between 10 and 1000 characters.", "danger")
    else:
        try:
            db.reviews.insert_one({
                "course_id": course["_id"], "student_id": user["_id"], "student_name": user["name"],
                "rating": rating, "comment": comment, "created_at": utcnow(),
            })
        except DuplicateKeyError:
            flash("You have already reviewed this course.", "info")
            return redirect(url_for("courses.detail", course_id=course["_id"]))
        if course.get("instructor_id"):
            notify(course["instructor_id"], "New course review",
                   f"{user['name']} rated {course['title']} {rating}/5.",
                   link=url_for("staff.course_manage", course_id=course["_id"]))
        log_activity(user["_id"], "student", "Review submitted", course["title"])
        flash("Thank you for your review!", "success")
    return redirect(url_for("courses.detail", course_id=course["_id"]))
@bp.route("/wishlist")
@login_required("student")
def wishlist():
    db = get_db()
    user = current_user()
    items = list(db.wishlists.find({"student_id": user["_id"]}).sort("added_at", -1))
    courses = {c["_id"]: c for c in db.courses.find({"_id": {"$in": [i["course_id"] for i in items]}})}
    cstats = course_stats_map()
    rows = [{"course": courses[i["course_id"]], "added_at": i["added_at"]}
            for i in items if i["course_id"] in courses]
    return render_template("wishlist.html", rows=rows, cstats=cstats,
                           instructors=users_map([r["course"].get("instructor_id") for r in rows]))
@bp.route("/courses/<course_id>/wishlist", methods=["POST"])
@login_required("student")
def wishlist_add(course_id):
    db = get_db()
    user = current_user()
    course = course_access(course_id, user)
    try:
        db.wishlists.insert_one({"student_id": user["_id"], "course_id": course["_id"],
                                 "added_at": utcnow()})
        flash("Added to your wishlist.", "success")
    except DuplicateKeyError:
        flash("This course is already in your wishlist.", "info")
    return redirect(request.referrer or url_for("courses.detail", course_id=course["_id"]))
@bp.route("/courses/<course_id>/wishlist/remove", methods=["POST"])
@login_required("student")
def wishlist_remove(course_id):
    user = current_user()
    get_db().wishlists.delete_one({"student_id": user["_id"], "course_id": oid(course_id)})
    flash("Removed from your wishlist.", "info")
    return redirect(request.referrer or url_for("courses.wishlist"))
@bp.route("/instructors/<instructor_id>")
def instructor(instructor_id):
    db = get_db()
    person = db.users.find_one({"_id": oid(instructor_id), "role": "staff"}, {"password_hash": 0})
    if not person:
        abort(404)
    courses = list(db.courses.find({"instructor_id": person["_id"], "status": "published"}))
    cids = [c["_id"] for c in courses]
    students = db.enrollments.count_documents({"course_id": {"$in": cids}})
    rating = list(db.reviews.aggregate([
        {"$match": {"course_id": {"$in": cids}}},
        {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
    ]))
    return render_template("instructor.html", person=person, courses=courses, students=students,
                           rating=round(rating[0]["avg"] or 0, 1) if rating else 0,
                           reviews=rating[0]["count"] if rating else 0,
                           cstats=course_stats_map())
