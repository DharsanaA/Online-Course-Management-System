"""Staff (instructor) area. Staff can only manage courses assigned to them."""
import re
from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from config import Config
from database.mongodb import get_db
from utils.analytics import course_analytics, course_student_rows, staff_dashboard_data
from utils.auth import current_user, require_role
from utils.files import remove_upload, save_upload
from utils.helpers import (from_local, local_input, log_activity, manageable_course, notify, notify_many,
                           oid, safe_int, upcoming_deadlines, utcnow)
bp = Blueprint("staff", __name__, url_prefix="/staff")
bp.before_request(require_role("staff"))
QUESTION_KEY = re.compile(r"^q(\d+)_text$")
def _enrolled_ids(course_id):
    return [e["student_id"] for e in get_db().enrollments.find({"course_id": course_id}, {"student_id": 1})]
def _quiz_values(quiz=None, form=None):
    """Values for the quiz form: from the submitted form, the stored quiz, or defaults."""
    if form is not None:
        return {"title": form.get("title", ""), "description": form.get("description", ""),
                "module_id": form.get("module_id", ""), "time_limit": form.get("time_limit", ""),
                "passing_percent": form.get("passing_percent", ""), "max_attempts": form.get("max_attempts", ""),
                "deadline_local": form.get("deadline", ""), "published": bool(form.get("published"))}
    if quiz:
        return {"title": quiz["title"], "description": quiz.get("description", ""),
                "module_id": str(quiz.get("module_id") or ""), "time_limit": quiz.get("time_limit") or 0,
                "passing_percent": quiz.get("passing_percent", Config.DEFAULT_PASSING_PERCENT),
                "max_attempts": quiz.get("max_attempts", 1), "deadline_local": local_input(quiz.get("deadline")),
                "published": quiz.get("published", False)}
    return {"title": "", "description": "", "module_id": "", "time_limit": 0,
            "passing_percent": Config.DEFAULT_PASSING_PERCENT, "max_attempts": 1,
            "deadline_local": "", "published": True}
def _assignment_values(assignment=None, form=None):
    if form is not None:
        return {"title": form.get("title", ""), "description": form.get("description", ""),
                "module_id": form.get("module_id", ""), "due_local": form.get("due_date", ""),
                "max_marks": form.get("max_marks", 100), "allow_late": bool(form.get("allow_late"))}
    if assignment:
        return {"title": assignment["title"], "description": assignment.get("description", ""),
                "module_id": str(assignment.get("module_id") or ""), "due_local": local_input(assignment["due_date"]),
                "max_marks": assignment.get("max_marks", 100), "allow_late": assignment.get("allow_late", True)}
    return {"title": "", "description": "", "module_id": "", "due_local": "", "max_marks": 100, "allow_late": True}
def _text(name, limit=200):
    return request.form.get(name, "").strip()[:limit]
def _form_datetime(name):
    try:
        return from_local(request.form.get(name, "").strip())
    except ValueError:
        return "invalid"
def _parse_questions(form):
    indexes = set()
    for key in form.keys():
        match = QUESTION_KEY.match(key)
        if match:
            indexes.add(int(match.group(1)))
    questions = []
    for i in sorted(indexes):
        text = form.get(f"q{i}_text", "").strip()
        if not text:
            continue
        options = [form.get(f"q{i}_opt{j}", "").strip()[:200] for j in range(4)]
        if not all(options):
            raise ValueError(f"Question {len(questions) + 1}: all four answer options are required.")
        correct = safe_int(form.get(f"q{i}_correct"), -1)
        if correct not in range(4):
            raise ValueError(f"Question {len(questions) + 1}: select the correct answer.")
        marks = safe_int(form.get(f"q{i}_marks"), 1, 1, 100)
        questions.append({"text": text[:500], "options": options, "correct_index": correct, "marks": marks})
    if not questions:
        raise ValueError("Add at least one question to the quiz.")
    return questions
@bp.route("/dashboard")
def dashboard():
    user = current_user()
    data = staff_dashboard_data(user["_id"])
    course_ids = [c["_id"] for c in data["courses"]]
    deadlines = upcoming_deadlines(course_ids, utcnow(), "staff") if course_ids else []
    activity = _staff_activity(course_ids)
    return render_template("staff/dashboard.html", data=data, deadlines=deadlines, activity=activity)
def _staff_activity(course_ids, limit=10):
    db = get_db()
    events = []
    titles = {c["_id"]: c["title"] for c in db.courses.find({"_id": {"$in": course_ids}}, {"title": 1})}
    for e in db.enrollments.find({"course_id": {"$in": course_ids}}).sort("enrolled_at", -1).limit(limit):
        student = db.users.find_one({"_id": e["student_id"]}, {"name": 1})
        events.append({"at": e["enrolled_at"], "text": f"New enrollment: {student['name'] if student else 'Student'} in {titles.get(e['course_id'], '')}"})
    for a in db.quiz_attempts.find({"course_id": {"$in": course_ids}}).sort("attempted_at", -1).limit(limit):
        events.append({"at": a["attempted_at"], "text": f"Quiz submission: {a['student_name']} ({a['percentage']}%)"})
    for s in db.assignment_submissions.find({"course_id": {"$in": course_ids}}).sort("submitted_at", -1).limit(limit):
        events.append({"at": s["submitted_at"], "text": f"Assignment submission: {s['student_name']}"})
    for r in db.reviews.find({"course_id": {"$in": course_ids}}).sort("created_at", -1).limit(limit):
        events.append({"at": r["created_at"], "text": f"Review received: {r['rating']}★ from {r['student_name']}"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
@bp.route("/courses")
def courses():
    return redirect(url_for("staff.dashboard"))
@bp.route("/courses/<course_id>")
def course_manage(course_id):
    db = get_db()
    user = current_user()
    course = manageable_course(course_id, user)
    cid = course["_id"]
    modules = list(db.modules.find({"course_id": cid}).sort("order", 1))
    lessons = list(db.lessons.find({"course_id": cid}).sort("order", 1))
    lessons_by_module = {}
    for lesson in lessons:
        lessons_by_module.setdefault(lesson.get("module_id"), []).append(lesson)
    materials = list(db.materials.find({"course_id": cid}).sort("uploaded_at", -1))
    quizzes = list(db.quizzes.find({"course_id": cid}, {"questions": 0}).sort("created_at", -1))
    assignments = list(db.assignments.find({"course_id": cid}).sort("due_date", 1))
    announcements = list(db.announcements.find({"course_id": cid}).sort("created_at", -1).limit(20))
    reviews = list(db.reviews.find({"course_id": cid}).sort("created_at", -1).limit(20))
    return render_template(
        "staff/course_manage.html", course=course, modules=modules, lessons=lessons,
        lessons_by_module=lessons_by_module, materials=materials, quizzes=quizzes,
        assignments=assignments, announcements=announcements, reviews=reviews,
        students=course_student_rows(cid), analytics=course_analytics(cid),
        module_map={m["_id"]: m["title"] for m in modules},
    )
@bp.route("/courses/<course_id>/analytics")
def analytics(course_id):
    course = manageable_course(course_id, current_user())
    return render_template("analytics/course_analytics.html", course=course,
                           data=course_analytics(course["_id"]), students=course_student_rows(course["_id"]),
                           back=url_for("staff.course_manage", course_id=course["_id"]))
@bp.route("/courses/<course_id>/modules", methods=["POST"])
def module_create(course_id):
    db = get_db()
    course = manageable_course(course_id, current_user())
    title = _text("title")
    if len(title) < 2:
        flash("Module title is required.", "danger")
    else:
        order = db.modules.count_documents({"course_id": course["_id"]}) + 1
        db.modules.insert_one({"course_id": course["_id"], "title": title,
                               "description": _text("description", 500), "order": order,
                               "created_at": utcnow()})
        flash("Module added.", "success")
    return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#content")
@bp.route("/modules/<module_id>/update", methods=["POST"])
def module_update(module_id):
    db = get_db()
    module = db.modules.find_one({"_id": oid(module_id)}) or abort(404)
    manageable_course(module["course_id"], current_user())
    db.modules.update_one({"_id": module["_id"]}, {"$set": {"title": _text("title") or module["title"],
                                                            "description": _text("description", 500)}})
    flash("Module updated.", "success")
    return redirect(url_for("staff.course_manage", course_id=module["course_id"]) + "#content")
@bp.route("/modules/<module_id>/delete", methods=["POST"])
def module_delete(module_id):
    db = get_db()
    module = db.modules.find_one({"_id": oid(module_id)}) or abort(404)
    manageable_course(module["course_id"], current_user())
    lesson_ids = [l["_id"] for l in db.lessons.find({"module_id": module["_id"]}, {"_id": 1})]
    db.progress.delete_many({"lesson_id": {"$in": lesson_ids}})
    db.materials.delete_many({"lesson_id": {"$in": lesson_ids}})
    db.lessons.delete_many({"module_id": module["_id"]})
    db.quizzes.update_many({"module_id": module["_id"]}, {"$set": {"module_id": None}})
    db.assignments.update_many({"module_id": module["_id"]}, {"$set": {"module_id": None}})
    db.modules.delete_one({"_id": module["_id"]})
    flash("Module and its lessons were deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=module["course_id"]) + "#content")
@bp.route("/courses/<course_id>/lessons", methods=["POST"])
def lesson_create(course_id):
    db = get_db()
    course = manageable_course(course_id, current_user())
    module_id = oid(request.form["module_id"]) if request.form.get("module_id") else None
    title = _text("title")
    content = request.form.get("content", "").strip()[:20000]
    if len(title) < 2 or not content:
        flash("Lesson title and content are required.", "danger")
        return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#content")
    if module_id and not db.modules.find_one({"_id": module_id, "course_id": course["_id"]}):
        abort(404)
    order = db.lessons.count_documents({"course_id": course["_id"]}) + 1
    result = db.lessons.insert_one({"course_id": course["_id"], "module_id": module_id, "title": title,
                                    "content": content, "order": order, "created_at": utcnow()})
    notify_many(_enrolled_ids(course["_id"]), "New lesson", f"New lesson in {course['title']}: {title}",
                link=url_for("student.lesson", course_id=course["_id"], lesson_id=result.inserted_id),
                kind="lesson")
    flash("Lesson published to enrolled students.", "success")
    return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#content")
@bp.route("/lessons/<lesson_id>/update", methods=["POST"])
def lesson_update(lesson_id):
    db = get_db()
    lesson = db.lessons.find_one({"_id": oid(lesson_id)}) or abort(404)
    manageable_course(lesson["course_id"], current_user())
    db.lessons.update_one({"_id": lesson["_id"]}, {"$set": {
        "title": _text("title") or lesson["title"],
        "content": request.form.get("content", "").strip()[:20000] or lesson["content"]}})
    flash("Lesson updated.", "success")
    return redirect(url_for("staff.course_manage", course_id=lesson["course_id"]) + "#content")
@bp.route("/lessons/<lesson_id>/delete", methods=["POST"])
def lesson_delete(lesson_id):
    db = get_db()
    lesson = db.lessons.find_one({"_id": oid(lesson_id)}) or abort(404)
    manageable_course(lesson["course_id"], current_user())
    db.progress.delete_many({"lesson_id": lesson["_id"]})
    for m in db.materials.find({"lesson_id": lesson["_id"]}):
        remove_upload(m["folder"], m["file_name"])
    db.materials.delete_many({"lesson_id": lesson["_id"]})
    db.lessons.delete_one({"_id": lesson["_id"]})
    flash("Lesson deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=lesson["course_id"]) + "#content")
@bp.route("/courses/<course_id>/materials", methods=["POST"])
def material_upload(course_id):
    db = get_db()
    user = current_user()
    course = manageable_course(course_id, user)
    kind = request.form.get("kind", "material")
    kind = kind if kind in ("note", "material") else "material"
    folder = "notes" if kind == "note" else "materials"
    lesson_id = oid(request.form["lesson_id"]) if request.form.get("lesson_id") else None
    if lesson_id and not db.lessons.find_one({"_id": lesson_id, "course_id": course["_id"]}):
        abort(404)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a file to upload.", "danger")
        return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#materials")
    try:
        stored, original = save_upload(file, folder, Config.DOCUMENT_EXTENSIONS, Config.DOCUMENT_MAX_BYTES)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#materials")
    db.materials.insert_one({
        "course_id": course["_id"], "lesson_id": lesson_id, "kind": kind, "folder": folder,
        "title": _text("title") or original, "file_name": stored, "original_name": original,
        "uploaded_by": user["_id"], "uploaded_at": utcnow(),
    })
    if lesson_id is None and kind == "note":
        notify_many(_enrolled_ids(course["_id"]), "Course notes available",
                    f"New overview notes for {course['title']}.",
                    link=url_for("courses.detail", course_id=course["_id"]), kind="material")
    flash("File uploaded.", "success")
    return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#materials")
@bp.route("/materials/<material_id>/delete", methods=["POST"])
def material_delete(material_id):
    db = get_db()
    material = db.materials.find_one({"_id": oid(material_id)}) or abort(404)
    manageable_course(material["course_id"], current_user())
    remove_upload(material["folder"], material["file_name"])
    db.materials.delete_one({"_id": material["_id"]})
    flash("File deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=material["course_id"]) + "#materials")
@bp.route("/courses/<course_id>/quizzes/new", methods=["GET", "POST"])
def quiz_create(course_id):
    course = manageable_course(course_id, current_user())
    return _quiz_form(course, None)
@bp.route("/quizzes/<quiz_id>/edit", methods=["GET", "POST"])
def quiz_edit(quiz_id):
    quiz = get_db().quizzes.find_one({"_id": oid(quiz_id)}) or abort(404)
    course = manageable_course(quiz["course_id"], current_user())
    return _quiz_form(course, quiz)
def _quiz_form(course, quiz):
    db = get_db()
    modules = list(db.modules.find({"course_id": course["_id"]}).sort("order", 1))
    if request.method == "POST":
        try:
            questions = _parse_questions(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("staff/quiz_form.html", course=course, quiz=quiz, modules=modules,
                                   values=_quiz_values(form=request.form),
                                   questions=_form_questions(request.form)), 400
        deadline = _form_datetime("deadline")
        if deadline == "invalid":
            flash("Enter a valid deadline.", "danger")
            return redirect(request.url)
        title = _text("title")
        if len(title) < 3:
            flash("Quiz title must be at least 3 characters.", "danger")
            return redirect(request.url)
        module_id = oid(request.form["module_id"]) if request.form.get("module_id") else None
        doc = {
            "course_id": course["_id"], "module_id": module_id, "title": title,
            "description": _text("description", 500),
            "time_limit": safe_int(request.form.get("time_limit"), 0, 0, 600) or None,
            "passing_percent": safe_int(request.form.get("passing_percent"), Config.DEFAULT_PASSING_PERCENT, 1, 100),
            "max_attempts": safe_int(request.form.get("max_attempts"), 1, 1, 10),
            "deadline": deadline, "published": bool(request.form.get("published")),
            "questions": questions,
        }
        if quiz:
            db.quizzes.update_one({"_id": quiz["_id"]}, {"$set": doc})
            flash("Quiz updated.", "success")
        else:
            doc.update({"created_by": current_user()["_id"], "created_at": utcnow()})
            db.quizzes.insert_one(doc)
            if doc["published"]:
                notify_many(_enrolled_ids(course["_id"]), "New quiz", f"New quiz available: {title}",
                            link=url_for("student.quizzes"), kind="quiz")
            flash("Quiz created.", "success")
        log_activity(current_user()["_id"], "staff", "Quiz saved", title)
        return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#quizzes")

    return render_template("staff/quiz_form.html", course=course, quiz=quiz, modules=modules,
                           values=_quiz_values(quiz), questions=(quiz or {}).get("questions", []))
def _form_questions(form):
    """Re-populate the builder after a validation error."""
    rows = []
    for key in sorted(form.keys()):
        m = QUESTION_KEY.match(key)
        if m:
            i = int(m.group(1))
            rows.append({"text": form.get(f"q{i}_text", ""),
                         "options": [form.get(f"q{i}_opt{j}", "") for j in range(4)],
                         "correct_index": safe_int(form.get(f"q{i}_correct"), -1),
                         "marks": safe_int(form.get(f"q{i}_marks"), 1)})
    return rows
@bp.route("/quizzes/<quiz_id>/delete", methods=["POST"])
def quiz_delete(quiz_id):
    db = get_db()
    quiz = db.quizzes.find_one({"_id": oid(quiz_id)}) or abort(404)
    manageable_course(quiz["course_id"], current_user())
    db.quiz_attempts.delete_many({"quiz_id": quiz["_id"]})
    db.quizzes.delete_one({"_id": quiz["_id"]})
    flash("Quiz and its attempts were deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=quiz["course_id"]) + "#quizzes")
@bp.route("/courses/<course_id>/assignments/new", methods=["GET", "POST"])
def assignment_create(course_id):
    course = manageable_course(course_id, current_user())
    return _assignment_form(course, None)
@bp.route("/assignments/<assignment_id>/edit", methods=["GET", "POST"])
def assignment_edit(assignment_id):
    assignment = get_db().assignments.find_one({"_id": oid(assignment_id)}) or abort(404)
    course = manageable_course(assignment["course_id"], current_user())
    return _assignment_form(course, assignment)
def _assignment_form(course, assignment):
    db = get_db()
    user = current_user()
    modules = list(db.modules.find({"course_id": course["_id"]}).sort("order", 1))
    if request.method == "POST":
        title = _text("title")
        due = _form_datetime("due_date")
        if len(title) < 3:
            flash("Assignment title must be at least 3 characters.", "danger")
            return render_template("staff/assignment_form.html", course=course, assignment=assignment,
                                   modules=modules, values=_assignment_values(form=request.form)), 400
        if due in (None, "invalid"):
            flash("Enter a valid due date and time.", "danger")
            return render_template("staff/assignment_form.html", course=course, assignment=assignment,
                                   modules=modules, values=_assignment_values(form=request.form)), 400
        doc = {
            "course_id": course["_id"],
            "module_id": oid(request.form["module_id"]) if request.form.get("module_id") else None,
            "title": title, "description": request.form.get("description", "").strip()[:5000],
            "due_date": due, "max_marks": safe_int(request.form.get("max_marks"), 100, 1, 1000),
            "allow_late": bool(request.form.get("allow_late")),
        }
        file = request.files.get("instruction_file")
        if file and file.filename:
            try:
                stored, original = save_upload(file, "assignments", Config.DOCUMENT_EXTENSIONS,
                                               Config.DOCUMENT_MAX_BYTES)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(request.url)
            if assignment:
                remove_upload("assignments", assignment.get("instruction_file"))
            doc.update({"instruction_file": stored, "instruction_name": original})
        if assignment:
            db.assignments.update_one({"_id": assignment["_id"]}, {"$set": doc})
            flash("Assignment updated.", "success")
        else:
            doc.update({"created_by": user["_id"], "created_at": utcnow()})
            db.assignments.insert_one(doc)
            notify_many(_enrolled_ids(course["_id"]), "New assignment",
                        f"New assignment: {title} (due {due:%d %b %Y}).",
                        link=url_for("student.assignments"), kind="assignment")
            flash("Assignment created and students notified.", "success")
        return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#assignments")
    return render_template("staff/assignment_form.html", course=course, assignment=assignment,
                           modules=modules, values=_assignment_values(assignment))
@bp.route("/assignments/<assignment_id>/delete", methods=["POST"])
def assignment_delete(assignment_id):
    db = get_db()
    assignment = db.assignments.find_one({"_id": oid(assignment_id)}) or abort(404)
    manageable_course(assignment["course_id"], current_user())
    for s in db.assignment_submissions.find({"assignment_id": assignment["_id"]}):
        remove_upload("submissions", s.get("file_name"))
    db.assignment_submissions.delete_many({"assignment_id": assignment["_id"]})
    remove_upload("assignments", assignment.get("instruction_file"))
    db.assignments.delete_one({"_id": assignment["_id"]})
    flash("Assignment deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=assignment["course_id"]) + "#assignments")
@bp.route("/assignments/<assignment_id>/submissions")
def submissions(assignment_id):
    db = get_db()
    assignment = db.assignments.find_one({"_id": oid(assignment_id)}) or abort(404)
    course = manageable_course(assignment["course_id"], current_user())
    subs = list(db.assignment_submissions.find({"assignment_id": assignment["_id"]}).sort("submitted_at", -1))
    return render_template("staff/submissions.html", course=course, assignment=assignment, subs=subs,
                           now=utcnow())
@bp.route("/submissions/<submission_id>/evaluate", methods=["POST"])
def evaluate(submission_id):
    db = get_db()
    sub = db.assignment_submissions.find_one({"_id": oid(submission_id)}) or abort(404)
    course = manageable_course(sub["course_id"], current_user())
    assignment = db.assignments.find_one({"_id": sub["assignment_id"]})
    try:
        marks = float(request.form.get("marks", ""))
    except ValueError:
        marks = -1
    if not 0 <= marks <= assignment["max_marks"]:
        flash(f"Marks must be between 0 and {assignment['max_marks']}.", "danger")
        return redirect(url_for("staff.submissions", assignment_id=assignment["_id"]))
    feedback = request.form.get("feedback", "").strip()[:3000]
    db.assignment_submissions.update_one({"_id": sub["_id"]}, {"$set": {
        "marks": marks, "feedback": feedback, "status": "evaluated", "evaluated_at": utcnow()}})
    notify(sub["student_id"], "Assignment result",
           f"{assignment['title']}: {marks}/{assignment['max_marks']} marks.", kind="assignment",
           link=url_for("student.assignments"))
    log_activity(current_user()["_id"], "staff", "Assignment evaluated", assignment["title"])
    flash("Evaluation saved and student notified.", "success")
    return redirect(url_for("staff.submissions", assignment_id=assignment["_id"]))
@bp.route("/courses/<course_id>/announcements", methods=["POST"])
def announcement_create(course_id):
    db = get_db()
    course = manageable_course(course_id, current_user())
    title = _text("title")
    message = request.form.get("message", "").strip()[:2000]
    if len(title) < 3 or not message:
        flash("Announcement title and message are required.", "danger")
    else:
        db.announcements.insert_one({"course_id": course["_id"], "title": title, "message": message,
                                     "created_by": current_user()["_id"], "created_by_name": current_user()["name"],
                                     "status": "published", "created_at": utcnow()})
        notify_many(_enrolled_ids(course["_id"]), f"Announcement: {title}", message[:140],
                    link=url_for("courses.detail", course_id=course["_id"]), kind="announcement")
        flash("Announcement posted to enrolled students.", "success")
    return redirect(url_for("staff.course_manage", course_id=course["_id"]) + "#announcements")
@bp.route("/announcements/<announcement_id>/delete", methods=["POST"])
def announcement_delete(announcement_id):
    db = get_db()
    ann = db.announcements.find_one({"_id": oid(announcement_id)}) or abort(404)
    if not ann.get("course_id"):
        abort(403)
    manageable_course(ann["course_id"], current_user())
    db.announcements.delete_one({"_id": ann["_id"]})
    flash("Announcement deleted.", "info")
    return redirect(url_for("staff.course_manage", course_id=ann["course_id"]) + "#announcements")
@bp.route("/reports")
def reports():
    return render_template("reports/index.html", scope="staff")
