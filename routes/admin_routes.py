"""Admin area: analytics dashboard, user/staff/student management, course management, moderation, settings."""
import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

from config import Config
from database.mongodb import get_db
from database.queries import (course_overview, course_structure, enrollment_count, enrollment_page,
                              index_demo)
from utils.analytics import admin_dashboard_data, course_analytics, course_student_rows
from utils.auth import current_user, require_role
from utils.files import remove_upload, save_upload
from utils.helpers import (EMAIL_RE, course_progress, course_stats_map, get_settings, log_activity, notify,
                           notify_many, oid, paginate, safe_int, users_map, utcnow, validate_password,
                           is_valid_oid)

bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.before_request(require_role("admin"))

ROLES = ("admin", "staff", "student")
DIFFICULTIES = ("Beginner", "Intermediate", "Advanced")
STATUSES = ("draft", "published", "archived")

@bp.route("/dashboard")
def dashboard():
    db = get_db()
    data = admin_dashboard_data()
    announcements = list(db.announcements.find({"course_id": None}).sort("created_at", -1).limit(5))
    return render_template("admin/dashboard.html", data=data, announcements=announcements)


@bp.route("/announcements", methods=["POST"])
def global_announcement():
    db = get_db()
    user = current_user()
    title = request.form.get("title", "").strip()[:150]
    message = request.form.get("message", "").strip()[:2000]
    if len(title) < 3 or not message:
        flash("Title and message are required.", "danger")
        return redirect(url_for("admin.dashboard"))
    db.announcements.insert_one({"course_id": None, "title": title, "message": message,
                                 "created_by": user["_id"], "created_by_name": user["name"],
                                 "status": "published", "created_at": utcnow()})
    students = [u["_id"] for u in db.users.find({"role": "student", "is_active": True}, {"_id": 1})]
    notify_many(students, f"Announcement: {title}", message[:140], kind="announcement")
    log_activity(user["_id"], "admin", "Global announcement", title)
    flash("Global announcement posted.", "success")
    return redirect(url_for("admin.dashboard"))

@bp.route("/users", methods=["GET", "POST"])
def users():
    db = get_db()
    if request.method == "POST":
        return _create_user()
    q = request.args.get("q", "").strip()[:80]
    role = request.args.get("role", "")
    query = {}
    if role in ROLES:
        query["role"] = role
    if q:
        pattern = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"name": pattern}, {"email": pattern}]
    total = db.users.count_documents(query)
    pg = paginate(total, safe_int(request.args.get("page"), 1, 1), Config.PER_PAGE)
    items = list(db.users.find(query, {"name": 1, "email": 1, "role": 1, "is_active": 1,
                                       "created_at": 1, "phone": 1, "profile_image": 1})
                 .sort("created_at", -1)
                 .skip(pg["skip"]).limit(pg["per_page"]))
    return render_template("admin/users.html", items=items, pg=pg, q=q, role=role, roles=ROLES)

def _create_user():
    db = get_db()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "student")
    password = request.form.get("password", "")
    error = None
    if len(name) < 2:
        error = "Name is required."
    elif not EMAIL_RE.match(email):
        error = "Enter a valid email address."
    elif role not in ROLES:
        error = "Choose a valid role."
    else:
        error = validate_password(password)
    if error:
        flash(error, "danger")
        return redirect(url_for("admin.users"))
    try:
        db.users.insert_one({
            "name": name, "email": email, "role": role,
            "password_hash": generate_password_hash(password),
            "phone": request.form.get("phone", "").strip()[:20], "bio": "", "expertise": "",
            "is_active": True, "created_at": utcnow(),
        })
    except DuplicateKeyError:
        flash("A user with this email already exists.", "danger")
        return redirect(url_for("admin.users"))
    log_activity(current_user()["_id"], "admin", "User created", f"{email} ({role})")
    flash(f"{role.title()} account created.", "success")
    return redirect(url_for("admin.users"))
@bp.route("/users/<user_id>")
def user_detail(user_id):
    db = get_db()
    person = db.users.find_one({"_id": oid(user_id)}, {"password_hash": 0}) or abort(404)
    context = {"person": person, "enrollments": [], "attempts": [], "certificates": [],
               "courses": [], "activity": list(db.activity_logs.find({"user_id": person["_id"]})
                                                .sort("created_at", -1).limit(20))}
    if person["role"] == "student":
        enrollments = list(db.enrollments.find({"student_id": person["_id"]}).sort("enrolled_at", -1))
        courses = {c["_id"]: c for c in db.courses.find({"_id": {"$in": [e["course_id"] for e in enrollments]}})}
        context["enrollments"] = [{"enrollment": e, "course": courses.get(e["course_id"]),
                                   "progress": course_progress(person["_id"], e["course_id"])}
                                  for e in enrollments if e["course_id"] in courses]
        context["attempts"] = list(db.quiz_attempts.find({"student_id": person["_id"]})
                                   .sort("attempted_at", -1).limit(20))
        context["certificates"] = list(db.certificates.find({"student_id": person["_id"]}))
    elif person["role"] == "staff":
        courses = list(db.courses.find({"instructor_id": person["_id"]}).sort("created_at", -1))
        stats = course_stats_map()
        context["courses"] = [{"course": c, "stats": stats.get(c["_id"], {})} for c in courses]
        context["student_total"] = sum(stats.get(c["_id"], {}).get("students", 0) for c in courses)
    return render_template("admin/user_detail.html", **context)


@bp.route("/users/<user_id>/update", methods=["POST"])
def user_update(user_id):
    db = get_db()
    person = db.users.find_one({"_id": oid(user_id)}) or abort(404)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", person["role"])
    if len(name) < 2 or not EMAIL_RE.match(email) or role not in ROLES:
        flash("Enter a valid name, email and role.", "danger")
        return redirect(url_for("admin.user_detail", user_id=person["_id"]))
    if person["role"] == "staff" and role != "staff" and db.courses.find_one({"instructor_id": person["_id"]}):
        flash("Reassign this instructor's courses before changing their role.", "warning")
        return redirect(url_for("admin.user_detail", user_id=person["_id"]))
    updates = {"name": name, "email": email, "role": role, "phone": request.form.get("phone", "").strip()[:20]}
    if role == "staff":
        updates["expertise"] = request.form.get("expertise", "").strip()[:300]
        updates["bio"] = request.form.get("bio", "").strip()[:1000]
    password = request.form.get("password", "")
    if password:
        problem = validate_password(password)
        if problem:
            flash(problem, "danger")
            return redirect(url_for("admin.user_detail", user_id=person["_id"]))
        updates["password_hash"] = generate_password_hash(password)
    try:
        db.users.update_one({"_id": person["_id"]}, {"$set": updates})
    except DuplicateKeyError:
        flash("That email is already used by another account.", "danger")
        return redirect(url_for("admin.user_detail", user_id=person["_id"]))
    log_activity(current_user()["_id"], "admin", "User updated", email)
    flash("User updated.", "success")
    return redirect(url_for("admin.user_detail", user_id=person["_id"]))


@bp.route("/users/<user_id>/toggle-active", methods=["POST"])
def user_toggle(user_id):
    db = get_db()
    person = db.users.find_one({"_id": oid(user_id)}) or abort(404)
    if person["_id"] == current_user()["_id"]:
        flash("You cannot deactivate your own account.", "danger")
    else:
        active = not person.get("is_active", True)
        db.users.update_one({"_id": person["_id"]}, {"$set": {"is_active": active}})
        log_activity(current_user()["_id"], "admin", "Account " + ("activated" if active else "deactivated"),
                     person["email"])
        flash(f"Account {'activated' if active else 'deactivated'}.", "info")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/users/<user_id>/delete", methods=["POST"])
def user_delete(user_id):
    db = get_db()
    person = db.users.find_one({"_id": oid(user_id)}) or abort(404)
    if person["_id"] == current_user()["_id"]:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.users"))
    if person["role"] == "staff" and db.courses.find_one({"instructor_id": person["_id"]}):
        flash("This instructor still owns courses. Reassign them first, or deactivate the account instead.", "warning")
        return redirect(url_for("admin.users"))
    if person["role"] == "student":
        # Remove all of the student's personal records; certificate files are kept for audit.
        for sub in db.assignment_submissions.find({"student_id": person["_id"]}, {"file_name": 1}):
            remove_upload("submissions", sub.get("file_name"))
        db.progress.delete_many({"student_id": person["_id"]})
        db.enrollments.delete_many({"student_id": person["_id"]})
        db.quiz_attempts.delete_many({"student_id": person["_id"]})
        db.assignment_submissions.delete_many({"student_id": person["_id"]})
        db.reviews.delete_many({"student_id": person["_id"]})
        db.wishlists.delete_many({"student_id": person["_id"]})
        db.notifications.delete_many({"user_id": person["_id"]})
    remove_upload("profiles", person.get("profile_image"))
    db.users.delete_one({"_id": person["_id"]})
    log_activity(current_user()["_id"], "admin", "User deleted", person["email"])
    flash("User deleted.", "info")
    return redirect(url_for("admin.users"))


# ---------- courses ----------
def _staff_choices():
    return list(get_db().users.find({"role": "staff", "is_active": True}, {"name": 1}).sort("name", 1))


@bp.route("/courses")
def courses():
    db = get_db()
    args = request.args
    q = args.get("q", "").strip()[:100]
    status = args.get("status", "")
    category = args.get("category", "").strip()
    instructor = args.get("instructor", "")
    query = {}
    if q:
        query["title"] = {"$regex": re.escape(q), "$options": "i"}
    if status in STATUSES:
        query["status"] = status
    if category:
        query["category"] = category
    if is_valid_oid(instructor):
        query["instructor_id"] = oid(instructor)
    total = db.courses.count_documents(query)
    pg = paginate(total, safe_int(args.get("page"), 1, 1), Config.PER_PAGE)
    items = list(db.courses.find(query).sort("created_at", -1).skip(pg["skip"]).limit(pg["per_page"]))
    return render_template("admin/courses.html", items=items, pg=pg, q=q, status=status,
                           category=category, instructor=instructor, statuses=STATUSES,
                           categories=sorted(c for c in db.courses.distinct("category") if c),
                           staff=_staff_choices(), stats=course_stats_map(),
                           instructors=users_map([c.get("instructor_id") for c in items]))


@bp.route("/courses/new", methods=["GET", "POST"])
def course_new():
    return _course_form(None)


@bp.route("/courses/<course_id>/edit", methods=["GET", "POST"])
def course_edit(course_id):
    course = get_db().courses.find_one({"_id": oid(course_id)}) or abort(404)
    return _course_form(course)


def _course_form(course):
    db = get_db()
    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()[:120]
        description = form.get("description", "").strip()[:2000]
        instructor_id = form.get("instructor_id", "")
        errors = []
        if len(title) < 3:
            errors.append("Course title must be at least 3 characters.")
        if len(description) < 10:
            errors.append("Add a short description (at least 10 characters).")
        if form.get("difficulty") not in DIFFICULTIES:
            errors.append("Choose a difficulty level.")
        if form.get("status") not in STATUSES:
            errors.append("Choose a valid status.")
        instructor = db.users.find_one({"_id": oid(instructor_id), "role": "staff"}) if is_valid_oid(instructor_id) else None
        if not instructor:
            errors.append("Assign an active instructor (staff account).")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/course_form.html", course=course, form=form,
                                   difficulties=DIFFICULTIES, statuses=STATUSES,
                                   staff=_staff_choices()), 400

        doc = {
            "title": title, "description": description,
            "overview": form.get("overview", "").strip()[:5000],
            "objectives": [line.strip() for line in form.get("objectives", "").splitlines() if line.strip()][:20],
            "category": form.get("category", "").strip()[:60] or "General",
            "difficulty": form.get("difficulty"),
            "duration_weeks": safe_int(form.get("duration_weeks"), 4, 1, 104),
            # Array of tags (lower-cased, de-duplicated) for tag filtering
            "tags": sorted({t.strip().lower()[:30] for t in form.get("tags", "").split(",") if t.strip()})[:10],
            # Embedded instructor document: copied from the staff user for fast course-card reads
            "instructor_id": instructor["_id"],
            "instructor": {"staff_id": instructor["_id"], "name": instructor["name"], "email": instructor["email"]},
            "status": form.get("status"),
            "updated_at": utcnow(),
        }
        image = request.files.get("image")
        if image and image.filename:
            try:
                stored, _ = save_upload(image, "course_images", Config.IMAGE_EXTENSIONS, Config.IMAGE_MAX_BYTES)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(request.url)
            if course:
                remove_upload("course_images", course.get("image"))
            doc["image"] = stored

        if course:
            db.courses.update_one({"_id": course["_id"]}, {"$set": doc})
            if course.get("instructor_id") != instructor["_id"]:
                # Keep the denormalised instructor on enrollments in sync for analytics.
                db.enrollments.update_many({"course_id": course["_id"]}, {"$set": {"instructor_id": instructor["_id"]}})
                notify(instructor["_id"], "Course assigned", f"You are now the instructor of {title}.",
                       link=url_for("staff.course_manage", course_id=course["_id"]), kind="info")
            course_id = course["_id"]
            flash("Course updated.", "success")
        else:
            doc.update({"created_by": current_user()["_id"], "created_at": utcnow(), "image": doc.get("image")})
            course_id = db.courses.insert_one(doc).inserted_id
            notify(instructor["_id"], "Course assigned", f"You were assigned to teach {title}.",
                   link=url_for("staff.course_manage", course_id=course_id), kind="info")
            flash("Course created. Add modules and lessons from the staff course manager.", "success")
        log_activity(current_user()["_id"], "admin", "Course saved", title)
        return redirect(url_for("admin.courses"))

    form = {}
    if course:
        form = {**course, "objectives": "\n".join(course.get("objectives", [])),
                "tags": ", ".join(course.get("tags", []))}
    return render_template("admin/course_form.html", course=course, form=form, difficulties=DIFFICULTIES,
                           statuses=STATUSES, staff=_staff_choices())


@bp.route("/courses/<course_id>/status", methods=["POST"])
def course_status(course_id):
    db = get_db()
    course = db.courses.find_one({"_id": oid(course_id)}) or abort(404)
    new_status = request.form.get("status")
    if new_status not in STATUSES:
        abort(400)
    db.courses.update_one({"_id": course["_id"]}, {"$set": {"status": new_status, "updated_at": utcnow()}})
    if new_status == "published":
        notify_many([u["_id"] for u in db.users.find({"role": "student", "is_active": True}, {"_id": 1})],
                    "New course available", f"{course['title']} is now open for enrollment.",
                    link=url_for("courses.detail", course_id=course["_id"]), kind="info")
    log_activity(current_user()["_id"], "admin", f"Course {new_status}", course["title"])
    flash(f"Course is now {new_status}.", "success")
    return redirect(request.referrer or url_for("admin.courses"))


@bp.route("/courses/<course_id>/delete", methods=["POST"])
def course_delete(course_id):
    db = get_db()
    course = db.courses.find_one({"_id": oid(course_id)}) or abort(404)
    if db.certificates.find_one({"course_id": course["_id"]}):
        flash("Courses with issued certificates cannot be deleted. Archive the course instead.", "warning")
        return redirect(url_for("admin.courses"))
    for m in db.materials.find({"course_id": course["_id"]}):
        remove_upload(m["folder"], m["file_name"])
    for a in db.assignments.find({"course_id": course["_id"]}):
        remove_upload("assignments", a.get("instruction_file"))
    for s in db.assignment_submissions.find({"course_id": course["_id"]}):
        remove_upload("submissions", s.get("file_name"))
    remove_upload("course_images", course.get("image"))
    for coll in ("modules", "lessons", "materials", "enrollments", "progress", "quizzes", "quiz_attempts",
                 "assignments", "assignment_submissions", "reviews", "wishlists", "announcements"):
        db[coll].delete_many({"course_id": course["_id"]})
    db.courses.delete_one({"_id": course["_id"]})
    log_activity(current_user()["_id"], "admin", "Course deleted", course["title"])
    flash("Course and all related records were deleted.", "info")
    return redirect(url_for("admin.courses"))


@bp.route("/courses/<course_id>/analytics")
def course_analytics_page(course_id):
    course = get_db().courses.find_one({"_id": oid(course_id)}) or abort(404)
    return render_template("analytics/course_analytics.html", course=course,
                           data=course_analytics(course["_id"]),
                           back=url_for("admin.courses"), students=course_student_rows(course["_id"]))

@bp.route("/reviews")
def reviews():
    db = get_db()
    q = request.args.get("q", "").strip()[:80]
    query = {}
    if q:
        query["$or"] = [{"comment": {"$regex": re.escape(q), "$options": "i"}},
                        {"student_name": {"$regex": re.escape(q), "$options": "i"}}]
    total = db.reviews.count_documents(query)
    pg = paginate(total, safe_int(request.args.get("page"), 1, 1), Config.PER_PAGE)
    items = list(db.reviews.find(query).sort("created_at", -1).skip(pg["skip"]).limit(pg["per_page"]))
    titles = {c["_id"]: c["title"] for c in db.courses.find({"_id": {"$in": [r["course_id"] for r in items]}},
                                                             {"title": 1})}
    return render_template("admin/reviews.html", items=items, pg=pg, q=q, titles=titles)


@bp.route("/reviews/<review_id>/delete", methods=["POST"])
def review_delete(review_id):
    db = get_db()
    review = db.reviews.find_one({"_id": oid(review_id)}) or abort(404)
    db.reviews.delete_one({"_id": review["_id"]})
    log_activity(current_user()["_id"], "admin", "Review removed", str(review["course_id"]))
    flash("Review removed.", "info")
    return redirect(request.referrer or url_for("admin.reviews"))


# ---------- logs, settings, reports ----------
@bp.route("/logs")
def logs():
    db = get_db()
    role = request.args.get("role", "")
    query = {"role": role} if role in ROLES else {}
    total = db.activity_logs.count_documents(query)
    pg = paginate(total, safe_int(request.args.get("page"), 1, 1), Config.PER_PAGE)
    items = list(db.activity_logs.find(query).sort("created_at", -1).skip(pg["skip"]).limit(pg["per_page"]))
    names = users_map([i["user_id"] for i in items])
    return render_template("admin/logs.html", items=items, pg=pg, role=role, roles=ROLES, names=names)


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    if request.method == "POST":
        form = request.form
        doc = {
            "name": form.get("name", "").strip()[:80] or "OCMS Learning",
            "description": form.get("description", "").strip()[:300],
            "contact_email": form.get("contact_email", "").strip()[:120],
            "certificate_org": form.get("certificate_org", "").strip()[:120] or "OCMS Learning Platform",
            "passing_percent": safe_int(form.get("passing_percent"), 60, 1, 100),
            "updated_at": utcnow(),
        }
        if doc["contact_email"] and not EMAIL_RE.match(doc["contact_email"]):
            flash("Enter a valid contact email.", "danger")
            return redirect(url_for("admin.settings"))
        db.settings.update_one({"_id": "platform"}, {"$set": doc}, upsert=True)
        log_activity(current_user()["_id"], "admin", "Settings updated")
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", settings=get_settings())


@bp.route("/reports")
def reports():
    return render_template("reports/index.html", scope="admin")


@bp.route("/enrollments")
def enrollments():
    """Paginated enrollment list. The data comes from an aggregation with $lookup (database/queries.py)."""
    db = get_db()
    total = enrollment_count(db)
    pg = paginate(total, safe_int(request.args.get("page"), 1, 1), Config.PER_PAGE)
    items = enrollment_page(db, pg["skip"], pg["per_page"])
    return render_template("admin/enrollments.html", items=items, pg=pg)


@bp.route("/database")
def database_insights():
    """NoSQL demonstration page: $lookup overview, $unwind structure and index explain plans."""
    db = get_db()
    return render_template("admin/database.html",
                           overview=course_overview(db),
                           structure=course_structure(db),
                           plans=index_demo(db))
