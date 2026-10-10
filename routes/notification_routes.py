"""Notifications page with unread count, mark-as-read and mark-all-read."""
from flask import Blueprint, abort, redirect, render_template, request, url_for
from database.mongodb import get_db
from utils.auth import current_user, login_required
from utils.helpers import oid, paginate, safe_int
bp = Blueprint("notifications", __name__)
@bp.route("/notifications")
@login_required()
def index():
    db = get_db()
    uid = current_user()["_id"]
    query = {"user_id": uid}
    if request.args.get("filter") == "unread":
        query["is_read"] = False
    total = db.notifications.count_documents(query)
    pg = paginate(total, safe_int(request.args.get("page"), 1, 1), 20)
    items = list(db.notifications.find(query).sort("created_at", -1).skip(pg["skip"]).limit(pg["per_page"]))
    return render_template("notifications.html", items=items, pg=pg,
                           unread=db.notifications.count_documents({"user_id": uid, "is_read": False}))
@bp.route("/notifications/<notification_id>/read", methods=["POST"])
@login_required()
def mark_read(notification_id):
    get_db().notifications.update_one(
        {"_id": oid(notification_id), "user_id": current_user()["_id"]}, {"$set": {"is_read": True}})
    link = request.form.get("next")
    if link and link.startswith("/") and not link.startswith("//"):
        return redirect(link)
    return redirect(url_for("notifications.index"))
@bp.route("/notifications/read-all", methods=["POST"])
@login_required()
def mark_all_read():
    get_db().notifications.update_many(
        {"user_id": current_user()["_id"], "is_read": False}, {"$set": {"is_read": True}})
    return redirect(request.referrer or url_for("notifications.index"))
