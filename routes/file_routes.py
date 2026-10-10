"""Secure file downloads and images. Every route checks the requester's permission first."""
import os
from flask import Blueprint, abort, send_file
from database.mongodb import get_db
from utils.auth import current_user, login_required
from utils.files import safe_upload_path
from utils.helpers import can_manage_course, is_enrolled, oid
bp = Blueprint("files", __name__, url_prefix="/files")
def _send(folder, stored, download_name, inline=False):
    path = safe_upload_path(folder, stored)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=not inline, download_name=download_name)
def _can_read_course(course, user):
    if can_manage_course(user, course):
        return True
    return user["role"] == "student" and is_enrolled(user["_id"], course["_id"])
@bp.route("/materials/<material_id>")
@login_required()
def material(material_id):
    db = get_db()
    user = current_user()
    item = db.materials.find_one({"_id": oid(material_id)}) or abort(404)
    course = db.courses.find_one({"_id": item["course_id"]}) or abort(404)
    overview_note = item.get("lesson_id") is None and course.get("status") == "published"
    if not (overview_note or _can_read_course(course, user)):
        abort(403)
    return _send(item["folder"], item["file_name"], item["original_name"])
@bp.route("/assignments/<assignment_id>/instructions")
@login_required()
def assignment_instructions(assignment_id):
    db = get_db()
    user = current_user()
    item = db.assignments.find_one({"_id": oid(assignment_id)}) or abort(404)
    course = db.courses.find_one({"_id": item["course_id"]}) or abort(404)
    if not _can_read_course(course, user):
        abort(403)
    if not item.get("instruction_file"):
        abort(404)
    return _send("assignments", item["instruction_file"], item["instruction_name"])
@bp.route("/submissions/<submission_id>")
@login_required("student", "staff", "admin")
def submission(submission_id):
    db = get_db()
    user = current_user()
    sub = db.assignment_submissions.find_one({"_id": oid(submission_id)}) or abort(404)
    if user["role"] == "student":
        if sub["student_id"] != user["_id"]:
            abort(403)
    else:
        course = db.courses.find_one({"_id": sub["course_id"]})
        if not can_manage_course(user, course):
            abort(403)
    if not sub.get("file_name"):
        abort(404)
    return _send("submissions", sub["file_name"], sub.get("original_name", "submission"))
@bp.route("/course-images/<course_id>")
def course_image(course_id):
    """Course covers are public only when the course is published."""
    db = get_db()
    course = db.courses.find_one({"_id": oid(course_id)}) or abort(404)
    if course.get("status") != "published":
        abort(404)
    if not course.get("image"):
        abort(404)
    return _send("course_images", course["image"], course["image"], inline=True)
@bp.route("/profiles/<user_id>")
@login_required()
def profile_image(user_id):
    person = get_db().users.find_one({"_id": oid(user_id)}) or abort(404)
    if not person.get("profile_image"):
        abort(404)
    return _send("profiles", person["profile_image"], person["profile_image"], inline=True)
