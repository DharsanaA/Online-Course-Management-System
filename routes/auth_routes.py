"""Registration, login, logout, profile editing and password change (all roles)."""
import hmac
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from database.mongodb import get_db
from utils.auth import current_user, home_for, login_required, login_user, logout_user, safe_next
from utils.files import remove_upload, save_upload
from utils.helpers import EMAIL_RE, log_activity, utcnow, validate_password
from config import Config

bp = Blueprint("auth", __name__)
ROLE_SIGNUP_CODES = {
    "student": None, 
    "staff": "STAFF_SIGNUP_CODE",
    "admin": "ADMIN_SIGNUP_CODE",
}
def _signup_code_ok(role, supplied):
    """Staff and admin signups need the matching secret code from .env."""
    config_key = ROLE_SIGNUP_CODES[role]
    if config_key is None:
        return True
    expected = getattr(Config, config_key, "")
    if not expected:
        return False  
    return hmac.compare_digest(supplied.encode(), expected.encode())
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(home_for(current_user()))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        role = request.form.get("role", "student")
        signup_code = request.form.get("signup_code", "")
        error = None
        if role not in ROLE_SIGNUP_CODES:
            error = "Please choose a valid role."
        elif len(name) < 2 or len(name) > 80:
            error = "Please enter your full name (2–80 characters)."
        elif not EMAIL_RE.match(email):
            error = "Please enter a valid email address."
        elif password != confirm:
            error = "Passwords do not match."
        elif not _signup_code_ok(role, signup_code):
            if ROLE_SIGNUP_CODES[role] and not getattr(Config, ROLE_SIGNUP_CODES[role]):
                error = f"{role.capitalize()} registration is not enabled on this site."
            else:
                error = f"Invalid {role} code. Ask the platform admin for the code."
        else:
            error = validate_password(password)
        if error:
            flash(error, "danger")
            return render_template("auth/register.html", form=request.form), 400

        db = get_db()
        if db.users.find_one({"email": email}, {"_id": 1}):
            flash("An account with this email already exists. Please log in.", "warning")
            return redirect(url_for("auth.login"))
        try:
            db.users.insert_one({
                "name": name, "email": email, "role": role,
                "password_hash": generate_password_hash(password),
                "phone": "", "bio": "", "is_active": True, "created_at": utcnow(),
            })
        except DuplicateKeyError:  # the unique index also guards against races
            flash("An account with this email already exists.", "warning")
            return redirect(url_for("auth.login"))
        flash("Registration successful. Please log in to start learning.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form={})
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(home_for(current_user()))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().users.find_one({"email": email})
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", email=email), 401
        if not user.get("is_active", True):
            flash("This account has been deactivated. Contact the administrator.", "danger")
            return render_template("auth/login.html", email=email), 403
        login_user(user)
        log_activity(user["_id"], user["role"], "Login")
        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(safe_next(request.args.get("next")) or home_for(user))
    return render_template("auth/login.html", email="")

@bp.route("/logout", methods=["POST"])
@login_required()
def logout():
    user = current_user()
    if user:
        log_activity(user["_id"], user["role"], "Logout")
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("courses.home"))

@bp.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    db = get_db()
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()[:20]
        bio = request.form.get("bio", "").strip()[:1000]
        expertise = request.form.get("expertise", "").strip()[:300]
        if len(name) < 2:
            flash("Name must be at least 2 characters.", "danger")
            return redirect(url_for("auth.profile"))
        updates = {"name": name, "phone": phone, "bio": bio}
        if user["role"] == "staff":
            updates["expertise"] = expertise
        image = request.files.get("profile_image")
        if image and image.filename:
            try:
                stored, _ = save_upload(image, "profiles", Config.IMAGE_EXTENSIONS, Config.IMAGE_MAX_BYTES)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("auth.profile"))
            remove_upload("profiles", user.get("profile_image"))
            updates["profile_image"] = stored
        db.users.update_one({"_id": user["_id"]}, {"$set": updates})
        if user["role"] == "staff":
            db.courses.update_many({"instructor.staff_id": user["_id"]}, {"$set": {"instructor.name": name}})
        log_activity(user["_id"], user["role"], "Profile updated")
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("profile.html", profile_user=user)
@bp.route("/profile/password", methods=["POST"])
@login_required()
def change_password():
    db = get_db()
    user = db.users.find_one({"_id": current_user()["_id"]})
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not check_password_hash(user["password_hash"], current):
        flash("Your current password is incorrect.", "danger")
    elif new != confirm:
        flash("New passwords do not match.", "danger")
    elif validate_password(new):
        flash(validate_password(new), "danger")
    elif check_password_hash(user["password_hash"], new):
        flash("Choose a password different from the current one.", "warning")
    else:
        db.users.update_one({"_id": user["_id"]},
                            {"$set": {"password_hash": generate_password_hash(new),
                                      "password_changed_at": datetime.utcnow()}})
        log_activity(user["_id"], user["role"], "Password changed")
        flash("Password changed successfully.", "success")
    return redirect(url_for("auth.profile"))
