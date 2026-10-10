"""Certificates: student list, PDF view/download, public verification."""
import os
from flask import Blueprint, abort, current_app, render_template, request, send_file
from database.mongodb import get_db
from utils.auth import current_user, login_required
from utils.helpers import CERT_ID_RE, oid
bp = Blueprint("certificates", __name__)
@bp.route("/student/certificates")
@login_required("student")
def my_certificates():
    db = get_db()
    certs = list(db.certificates.find({"student_id": current_user()["_id"]}).sort("issued_at", -1))
    return render_template("student/certificates.html", certs=certs)
@bp.route("/certificates/<cert_id>/pdf")
@login_required("student", "admin")
def certificate_pdf(cert_id):
    """Serve the stored ReportLab PDF. ?download=1 forces a download; otherwise it opens inline."""
    db = get_db()
    user = current_user()
    cert = db.certificates.find_one({"_id": oid(cert_id)})
    if not cert:
        abort(404)
    if user["role"] == "student" and cert["student_id"] != user["_id"]:
        abort(403)
    path = os.path.join(current_app.config["UPLOAD_ROOT"], "certificates", os.path.basename(cert["file_name"]))
    if not os.path.isfile(path):
        abort(404)
    download = request.args.get("download") == "1"
    return send_file(path, mimetype="application/pdf", as_attachment=download,
                     download_name=f"{cert['certificate_id']}.pdf")

@bp.route("/verify")
def verify():
    """Public certificate verification. Accepts ?certificate_id=... for shareable links."""
    raw = (request.values.get("certificate_id") or "").strip().upper()
    result = None
    if raw:
        cert = None
        if CERT_ID_RE.match(raw):
            cert = get_db().certificates.find_one({"certificate_id": raw})
        result = {"valid": bool(cert), "cert": cert, "searched": raw}
    return render_template("verify.html", result=result, certificate_id=raw)
