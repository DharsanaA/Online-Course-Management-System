"""Student area: dashboard, My Learning, lessons, continue learning, quizzes and assignments."""
from datetime import datetime
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from pymongo import ASCENDING
from config import Config
from database.mongodb import get_db
from utils.auth import current_user, login_required
from utils.documents import issue_certificate_if_eligible
from utils.files import save_upload
from utils.helpers import (course_progress, course_access, course_stats_map, is_enrolled, log_activity,
                           notify, oid, upcoming_deadlines, users_map, utcnow, can_manage_course)
bp = Blueprint("student", __name__, url_prefix="")
def _enrolled_course_ids(uid):
    return [e["course_id"] for e in get_db().enrollments.find({"student_id": uid}, {"course_id": 1})]
def _resume_lesson_id(uid, cid):
    """Last unfinished lesson: the stored last-accessed lesson if still open, else the first unfinished one."""
    db = get_db()
    enrollment = db.enrollments.find_one({"student_id": uid, "course_id": cid}) or {}
    done = set(db.progress.distinct("lesson_id", {"student_id": uid, "course_id": cid}))
    lessons = list(db.lessons.find({"course_id": cid}).sort("order", ASCENDING))
    if not lessons:
        return None
    last = enrollment.get("last_lesson_id")
    if last and last not in done and any(l["_id"] == last for l in lessons):
        return last
    for lesson in lessons:
        if lesson["_id"] not in done:
            return lesson["_id"]
    return lessons[-1]["_id"]


def _require_enrolled(uid, cid):
    if not is_enrolled(uid, cid):
        abort(403)
@bp.route("/student/dashboard")
@login_required("student")
def dashboard():
    db = get_db()
    user = current_user()
    uid = user["_id"]
    enrollments = list(db.enrollments.find({"student_id": uid}).sort("enrolled_at", -1))
    course_ids = [e["course_id"] for e in enrollments]
    courses = {c["_id"]: c for c in db.courses.find({"_id": {"$in": course_ids}})}
    rows = []
    for e in enrollments:
        course = courses.get(e["course_id"])
        if course:
            rows.append({"course": course, "progress": course_progress(uid, course["_id"]),
                         "enrollment": e})
    completed = sum(1 for r in rows if r["progress"]["status"] == "COMPLETED")
    avg = list(db.quiz_attempts.aggregate([
        {"$match": {"student_id": uid}},
        {"$group": {"_id": None, "avg": {"$avg": "$percentage"}}},
    ]))
    avg_score = round(avg[0]["avg"], 1) if avg and avg[0]["avg"] is not None else None
    last = db.enrollments.find_one({"student_id": uid, "last_lesson_id": {"$ne": None}},
                                   sort=[("last_accessed", -1)])
    continue_item = None
    if last and last["course_id"] in courses:
        lesson = db.lessons.find_one({"_id": last["last_lesson_id"]})
        if lesson:
            module = db.modules.find_one({"_id": lesson.get("module_id")})
            continue_item = {
                "course": courses[last["course_id"]], "lesson": lesson,
                "module": module,
                "progress": course_progress(uid, last["course_id"]),
            }
    recommended = _recommend(uid, course_ids, rows)
    deadlines = upcoming_deadlines(course_ids, utcnow(), "student")
    activity = list(db.activity_logs.find({"user_id": uid}).sort("created_at", -1).limit(8))
    recent_attempts = list(db.quiz_attempts.find({"student_id": uid}).sort("attempted_at", -1).limit(5))
    certificates = list(db.certificates.find({"student_id": uid}).sort("issued_at", -1).limit(3))
    wishlist_items = list(db.wishlists.find({"student_id": uid}).sort("added_at", -1).limit(4))
    wish_rows = list(db.courses.find({"_id": {"$in": [w["course_id"] for w in wishlist_items]}}))
    return render_template(
        "student/dashboard.html",
        rows=rows, completed=completed, in_progress=len(rows) - completed,
        avg_score=avg_score, cert_count=db.certificates.count_documents({"student_id": uid}),
        wishlist_count=db.wishlists.count_documents({"student_id": uid}),
        continue_item=continue_item, recommended=recommended, deadlines=deadlines,
        activity=activity, recent_attempts=recent_attempts, certificates=certificates,
        wish_rows=wish_rows, cstats=course_stats_map(),
        chart={"labels": [r["course"]["title"][:24] for r in rows],
               "values": [r["progress"]["percent"] for r in rows]},
    )
def _recommend(uid, enrolled_ids, rows, limit=4):
    db = get_db()
    categories = list({r["course"].get("category") for r in rows if r["course"].get("category")})
    exclude = list(enrolled_ids)
    picks = []
    if categories:
        picks = list(db.courses.find({"status": "published", "_id": {"$nin": exclude},
                                      "category": {"$in": categories}}).limit(limit))
    if len(picks) < limit:
        exclude += [p["_id"] for p in picks]
        picks += list(db.courses.find({"status": "published", "_id": {"$nin": exclude}})
                      .limit(limit - len(picks)))
    stats = course_stats_map()
    picks.sort(key=lambda c: (stats.get(c["_id"], {}).get("rating", 0),
                              stats.get(c["_id"], {}).get("students", 0)), reverse=True)
    return picks
@bp.route("/student/my-learning")
@login_required("student")
def my_learning():
    db = get_db()
    user = current_user()
    uid = user["_id"]
    enrollments = list(db.enrollments.find({"student_id": uid}).sort("enrolled_at", -1))
    courses = {c["_id"]: c for c in db.courses.find({"_id": {"$in": [e["course_id"] for e in enrollments]}})}
    rows = []
    for e in enrollments:
        course = courses.get(e["course_id"])
        if not course:
            continue
        progress = course_progress(uid, course["_id"])
        rows.append({
            "course": course, "enrollment": e, "progress": progress,
            "resume_id": _resume_lesson_id(uid, course["_id"]),
            "instructor": users_map([course.get("instructor_id")]).get(course.get("instructor_id")),
        })
    return render_template("student/my_learning.html", rows=rows)
@bp.route("/learn/<course_id>/continue")
@login_required("student")
def continue_learning(course_id):
    cid = oid(course_id)
    user = current_user()
    _require_enrolled(user["_id"], cid)
    lesson_id = _resume_lesson_id(user["_id"], cid)
    if not lesson_id:
        flash("This course does not have any lessons yet.", "info")
        return redirect(url_for("courses.detail", course_id=cid))
    return redirect(url_for("student.lesson", course_id=cid, lesson_id=lesson_id))
@bp.route("/learn/<course_id>/lessons/<lesson_id>")
@login_required("student", "staff", "admin")
def lesson(course_id, lesson_id):
    db = get_db()
    user = current_user()
    course = course_access(course_id, user)
    cid = course["_id"]
    lid = oid(lesson_id)
    if user["role"] == "student":
        if not is_enrolled(user["_id"], cid):
            flash("Enroll in this course to open its lessons.", "warning")
            return redirect(url_for("courses.detail", course_id=cid))
    elif not can_manage_course(user, course):
        abort(403)
    lessons = list(db.lessons.find({"course_id": cid}).sort("order", ASCENDING))
    index = next((i for i, l in enumerate(lessons) if l["_id"] == lid), None)
    if index is None:
        abort(404)
    current = lessons[index]
    prev_lesson = lessons[index - 1] if index > 0 else None
    next_lesson = lessons[index + 1] if index + 1 < len(lessons) else None
    module = db.modules.find_one({"_id": current.get("module_id")})
    materials = list(db.materials.find({"lesson_id": lid}))
    completed = False
    progress = None
    done_ids = []
    if user["role"] == "student":
        db.enrollments.update_one({"student_id": user["_id"], "course_id": cid},
                                  {"$set": {"last_lesson_id": lid, "last_accessed": utcnow()}})
        done_ids = db.progress.distinct("lesson_id", {"student_id": user["_id"], "course_id": cid})
        completed = lid in done_ids
        progress = course_progress(user["_id"], cid)
    return render_template("student/lesson.html", course=course, lesson=current, module=module,
                           lessons=lessons, index=index, prev_lesson=prev_lesson,
                           next_lesson=next_lesson, materials=materials, completed=completed,
                           progress=progress, done_ids=done_ids)
@bp.route("/learn/<course_id>/lessons/<lesson_id>/complete", methods=["POST"])
@login_required("student")
def complete_lesson(course_id, lesson_id):
    db = get_db()
    user = current_user()
    cid = oid(course_id)
    lid = oid(lesson_id)
    _require_enrolled(user["_id"], cid)
    lesson = db.lessons.find_one({"_id": lid, "course_id": cid})
    if not lesson:
        abort(404)
    db.progress.update_one(
        {"student_id": user["_id"], "lesson_id": lid},
        {"$setOnInsert": {"course_id": cid, "completed_at": utcnow()}},
        upsert=True,
    )
    db.enrollments.update_one({"student_id": user["_id"], "course_id": cid},
                              {"$addToSet": {"completed_lessons": lid}})
    log_activity(user["_id"], "student", "Completed lesson", lesson["title"])

    certificate = issue_certificate_if_eligible(user["_id"], cid)
    if certificate:
        flash("Course complete! Your certificate has been generated.", "success")
    else:
        flash("Lesson marked as completed.", "success")

    following = db.lessons.find_one({"course_id": cid, "order": {"$gt": lesson.get("order", 0)}},
                                    sort=[("order", ASCENDING)])
    if following:
        return redirect(url_for("student.lesson", course_id=cid, lesson_id=following["_id"]))
    return redirect(url_for("student.my_learning"))
def _quiz_for_student(quiz_id):
    db = get_db()
    user = current_user()
    quiz = db.quizzes.find_one({"_id": oid(quiz_id), "published": True})
    if not quiz:
        abort(404)
    _require_enrolled(user["_id"], quiz["course_id"])
    return quiz
@bp.route("/student/quizzes")
@login_required("student")
def quizzes():
    db = get_db()
    uid = current_user()["_id"]
    course_ids = _enrolled_course_ids(uid)
    courses = {c["_id"]: c["title"] for c in db.courses.find({"_id": {"$in": course_ids}}, {"title": 1})}
    available = []
    for quiz in db.quizzes.find({"course_id": {"$in": course_ids}, "published": True}, {"questions": 0}) \
            .sort("deadline", ASCENDING):
        used = db.quiz_attempts.count_documents({"student_id": uid, "quiz_id": quiz["_id"]})
        best = list(db.quiz_attempts.aggregate([
            {"$match": {"student_id": uid, "quiz_id": quiz["_id"]}},
            {"$group": {"_id": None, "best": {"$max": "$percentage"}}},
        ]))
        available.append({"quiz": quiz, "course": courses.get(quiz["course_id"], ""), "used": used,
                          "best": best[0]["best"] if best else None})
    history = list(db.quiz_attempts.find({"student_id": uid}).sort("attempted_at", -1))
    titles = {q["_id"]: q["title"] for q in db.quizzes.find({"_id": {"$in": [h["quiz_id"] for h in history]}},
                                                             {"title": 1})}
    return render_template("student/quizzes.html", available=available, history=history, titles=titles,
                           courses=courses)
@bp.route("/quizzes/<quiz_id>")
@login_required("student")
def quiz_take(quiz_id):
    quiz = _quiz_for_student(quiz_id)
    uid = current_user()["_id"]
    now = utcnow()
    if quiz.get("deadline") and now > quiz["deadline"]:
        flash("The deadline for this quiz has passed.", "warning")
        return redirect(url_for("student.quizzes"))
    used = get_db().quiz_attempts.count_documents({"student_id": uid, "quiz_id": quiz["_id"]})
    if used >= quiz.get("max_attempts", 1):
        flash("You have used all attempts for this quiz.", "warning")
        return redirect(url_for("student.quizzes"))

    key = f"quiz_start_{quiz['_id']}"
    if key not in session:
        session[key] = now.isoformat()
    started = datetime.fromisoformat(session[key])
    remaining = None
    if quiz.get("time_limit"):
        remaining = max(0, quiz["time_limit"] * 60 - int((now - started).total_seconds()))
    public_questions = [{"text": q["text"], "options": q["options"]} for q in quiz["questions"]]
    return render_template("student/quiz_take.html", quiz=quiz, questions=public_questions,
                           remaining=remaining, attempts_left=quiz.get("max_attempts", 1) - used)
@bp.route("/quizzes/<quiz_id>/submit", methods=["POST"])
@login_required("student")
def quiz_submit(quiz_id):
    db = get_db()
    user = current_user()
    quiz = _quiz_for_student(quiz_id)
    uid = user["_id"]
    now = utcnow()
    used = db.quiz_attempts.count_documents({"student_id": uid, "quiz_id": quiz["_id"]})
    if used >= quiz.get("max_attempts", 1):
        flash("You have used all attempts for this quiz.", "warning")
        return redirect(url_for("student.quizzes"))
    key = f"quiz_start_{quiz['_id']}"
    if key not in session:
        flash("Start the quiz before submitting it.", "warning")
        return redirect(url_for("student.quiz_take", quiz_id=quiz["_id"]))
    started = datetime.fromisoformat(session[key])
    elapsed = (now - started).total_seconds()
    limit = quiz.get("time_limit")
    timed_out = bool(limit) and elapsed > limit * 60 + Config.QUIZ_GRACE_SECONDS
    questions = quiz["questions"]
    answers = []
    for i, q in enumerate(questions):
        raw = request.form.get(f"q{i}", "")
        try:
            value = int(raw)
        except ValueError:
            value = -1
        answers.append(value if 0 <= value < len(q["options"]) else -1)

    if not timed_out and any(a == -1 for a in answers):
        flash("Please answer every question before submitting.", "danger")
        return redirect(url_for("student.quiz_take", quiz_id=quiz["_id"]))

    total = sum(q["marks"] for q in questions)
    score = 0
    if not timed_out:
        score = sum(q["marks"] for q, a in zip(questions, answers) if a == q["correct_index"])
    percentage = round(score / total * 100, 2) if total else 0
    passing = quiz.get("passing_percent", Config.DEFAULT_PASSING_PERCENT)
    passed = (not timed_out) and percentage >= passing

    result = db.quiz_attempts.insert_one({
        "quiz_id": quiz["_id"], "course_id": quiz["course_id"], "student_id": uid,
        "student_name": user["name"], "answers": answers, "score": score, "total_marks": total,
        "percentage": percentage, "passing_percent": passing, "passed": passed,
        "timed_out": timed_out, "attempt_no": used + 1, "attempted_at": now,
        "time_taken_seconds": int(elapsed),
    })
    session.pop(key, None)

    verdict = "Passed" if passed else ("Time expired" if timed_out else "Not passed")
    notify(uid, "Quiz result", f"{quiz['title']}: {percentage}% ({verdict}).", kind="quiz",
           link=url_for("student.quiz_result", attempt_id=result.inserted_id))
    course = db.courses.find_one({"_id": quiz["course_id"]}, {"instructor_id": 1, "title": 1})
    if course and course.get("instructor_id"):
        notify(course["instructor_id"], "Quiz submitted",
               f"{user['name']} submitted {quiz['title']} ({percentage}%).",
               link=url_for("staff.course_manage", course_id=quiz["course_id"]))
    log_activity(uid, "student", "Quiz submission", quiz["title"])
    issue_certificate_if_eligible(uid, quiz["course_id"])
    flash(f"Quiz submitted: {score}/{total} marks ({percentage}%) — {verdict}.",
          "success" if passed else "warning")
    return redirect(url_for("student.quiz_result", attempt_id=result.inserted_id))


@bp.route("/quiz-attempts/<attempt_id>")
@login_required("student", "staff", "admin")
def quiz_result(attempt_id):
    db = get_db()
    user = current_user()
    attempt = db.quiz_attempts.find_one({"_id": oid(attempt_id)})
    if not attempt:
        abort(404)
    if user["role"] == "student":
        if attempt["student_id"] != user["_id"]:
            abort(403)
    else:
        course = db.courses.find_one({"_id": attempt["course_id"]})
        if not can_manage_course(user, course):
            abort(403)
    quiz = db.quizzes.find_one({"_id": attempt["quiz_id"]})
    review = []
    if quiz:
        for i, q in enumerate(quiz["questions"]):
            given = attempt["answers"][i] if i < len(attempt["answers"]) else -1
            review.append({"text": q["text"], "options": q["options"], "given": given,
                           "correct": q["correct_index"], "marks": q["marks"],
                           "is_correct": given == q["correct_index"]})
    return render_template("student/quiz_result.html", attempt=attempt, quiz=quiz, review=review)


# ---------- assignments ----------
@bp.route("/student/assignments")
@login_required("student")
def assignments():
    db = get_db()
    uid = current_user()["_id"]
    course_ids = _enrolled_course_ids(uid)
    courses = {c["_id"]: c["title"] for c in db.courses.find({"_id": {"$in": course_ids}}, {"title": 1})}
    items = list(db.assignments.find({"course_id": {"$in": course_ids}}).sort("due_date", ASCENDING))
    subs = {s["assignment_id"]: s for s in db.assignment_submissions.find(
        {"student_id": uid, "assignment_id": {"$in": [a["_id"] for a in items]}})}
    now = utcnow()
    rows = []
    for a in items:
        sub = subs.get(a["_id"])
        if sub:
            status = {"submitted": "Submitted", "late": "Late", "evaluated": "Evaluated"}[sub["status"]]
        else:
            status = "Pending"
        rows.append({"assignment": a, "submission": sub, "status": status,
                     "overdue": (not sub) and now > a["due_date"],
                     "course": courses.get(a["course_id"], "")})
    return render_template("student/assignments.html", rows=rows, now=now)


@bp.route("/assignments/<assignment_id>/submit", methods=["POST"])
@login_required("student")
def submit_assignment(assignment_id):
    db = get_db()
    user = current_user()
    assignment = db.assignments.find_one({"_id": oid(assignment_id)})
    if not assignment:
        abort(404)
    _require_enrolled(user["_id"], assignment["course_id"])
    existing = db.assignment_submissions.find_one({"assignment_id": assignment["_id"], "student_id": user["_id"]})
    if existing and existing.get("status") == "evaluated":
        flash("This assignment has already been evaluated and can no longer be changed.", "warning")
        return redirect(url_for("student.assignments"))

    now = utcnow()
    late = now > assignment["due_date"]
    if late and not assignment.get("allow_late", True):
        flash("The deadline has passed and late submissions are not accepted.", "danger")
        return redirect(url_for("student.assignments"))

    text = request.form.get("answer_text", "").strip()[:5000]
    file = request.files.get("submission_file")
    stored = original = None
    if file and file.filename:
        try:
            stored, original = save_upload(file, "submissions", Config.DOCUMENT_EXTENSIONS,
                                           Config.DOCUMENT_MAX_BYTES)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("student.assignments"))
    if not text and not stored and not (existing and existing.get("file_name")):
        flash("Write an answer or upload a file before submitting.", "danger")
        return redirect(url_for("student.assignments"))

    update = {"course_id": assignment["course_id"], "student_name": user["name"],
              "answer_text": text, "status": "late" if late else "submitted", "submitted_at": now}
    if stored:
        update.update({"file_name": stored, "original_name": original})
    db.assignment_submissions.update_one(
        {"assignment_id": assignment["_id"], "student_id": user["_id"]},
        {"$set": update, "$setOnInsert": {"student_id": user["_id"], "assignment_id": assignment["_id"]}},
        upsert=True,
    )
    course = db.courses.find_one({"_id": assignment["course_id"]}, {"instructor_id": 1, "title": 1})
    if course and course.get("instructor_id"):
        notify(course["instructor_id"], "Assignment submitted",
               f"{user['name']} submitted {assignment['title']}.",
               link=url_for("staff.submissions", assignment_id=assignment["_id"]))
    log_activity(user["_id"], "student", "Assignment submission", assignment["title"])
    # The assignment may have been the last requirement for the course.
    if issue_certificate_if_eligible(user["_id"], assignment["course_id"]):
        flash("Course complete! Your certificate has been generated.", "success")
    else:
        flash("Assignment submitted successfully." + (" (Marked as late.)" if late else ""), "success")
    return redirect(url_for("student.assignments"))
