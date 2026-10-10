"""Authentication, session handling, CSRF protection and role-based access control."""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse
from bson import ObjectId
from bson.errors import InvalidId
from flask import abort, flash, g, redirect, request, session, url_for
from database.mongodb import get_db
ROLE_HOME = {
    "admin": "admin.dashboard",
    "staff": "staff.dashboard",
    "student": "student.dashboard",
}
def current_user():
    """Return the logged-in user document (without password hash), or None. Cached per request."""
    if not hasattr(g, "current_user"):
        g.current_user = None
        uid = session.get("user_id")
        if uid:
            try:
                user = get_db().users.find_one({"_id": ObjectId(uid)}, {"password_hash": 0})
            except InvalidId:
                user = None
            if user and user.get("is_active", True):
                g.current_user = user
            else:
                session.clear()
    return g.current_user
def login_user(user):
    """Start a fresh session. Clearing first prevents session fixation."""
    session.clear()
    session.permanent = True
    session["user_id"] = str(user["_id"])
    session["_csrf"] = secrets.token_hex(16)
def logout_user():
    session.clear()
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]
def check_csrf():
    """Validate the csrf_token field on every POST form."""
    sent = request.form.get("csrf_token", "")
    expected = session.get("_csrf", "")
    return bool(expected) and hmac.compare_digest(sent, expected)
def safe_next(target):
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme or not target.startswith("/") or target.startswith("//"):
        return None
    return target
def require_role(*roles):
    """Gate used as a blueprint-level before_request hook."""
    def check():
        user = current_user()
        if not user:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        if user["role"] not in roles:
            abort(403)
    return check
def login_required(*roles):
    """Route decorator. With no roles it only requires a logged-in user."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if roles and user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
def home_for(user):
    return url_for(ROLE_HOME.get(user["role"], "courses.home")) if user else url_for("courses.home")
