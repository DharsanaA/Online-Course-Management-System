"""Application factory and entry point.

Run locally:  python app.py   (or: flask --app app run --debug)
"""
import os

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from config import Config
from database.indexes import create_indexes
from database.mongodb import init_db
from routes import (admin_routes, auth_routes, certificate_routes, course_routes, file_routes,
                    notification_routes, report_routes, staff_routes, student_routes)
from utils.auth import check_csrf, csrf_token, current_user
from utils.files import UPLOAD_FOLDERS
from utils.helpers import fmt_date, fmt_dt, get_settings, unread_count

ERROR_MESSAGES = {
    400: ("Something went wrong", "Your form session expired or the request was invalid. Please try again."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "The page or record you are looking for does not exist or has been moved."),
    405: ("Method not allowed", "This action is not available here."),
    413: ("File too large", "The file you uploaded exceeds the allowed size."),
    500: ("Server error", "Something went wrong on our side. Please try again in a moment."),
}


def create_app():
    Config.validate()
    app = Flask(__name__)
    app.config.from_object(Config)

    for folder in UPLOAD_FOLDERS:
        os.makedirs(os.path.join(app.config["UPLOAD_ROOT"], folder), exist_ok=True)

    db = init_db(app)
    create_indexes(db)

    for module in (auth_routes, course_routes, student_routes, staff_routes, admin_routes,
                   certificate_routes, notification_routes, file_routes, report_routes):
        app.register_blueprint(module.bp)

    app.jinja_env.filters["dt"] = fmt_dt
    app.jinja_env.filters["date"] = fmt_date

    @app.before_request
    def protect_against_csrf():
        if request.method == "POST" and not check_csrf():
            abort(400)

    @app.context_processor
    def inject_globals():
        user = current_user()

        def csrf_field():
            return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')

        return {
            "user": user,
            "platform": get_settings(),
            "csrf_field": csrf_field,
            "unread_count": unread_count(user),
        }

    def render_error(code):
        title, message = ERROR_MESSAGES.get(code, ERROR_MESSAGES[500])
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        return render_error(exc.code)

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        app.logger.exception("Unhandled error: %s", exc)
        return render_error(500)

    @app.cli.command("create-admin")
    def create_admin():
        """Create the first administrator account (the database starts empty)."""
        import getpass
        from werkzeug.security import generate_password_hash
        from utils.helpers import EMAIL_RE, utcnow, validate_password
        email = input("Admin email: ").strip().lower()
        if not EMAIL_RE.match(email):
            raise SystemExit("Invalid email address.")
        if db.users.find_one({"email": email}):
            raise SystemExit("A user with this email already exists.")
        password = getpass.getpass("Password: ")
        problem = validate_password(password)
        if problem:
            raise SystemExit(problem)
        db.users.insert_one({"name": "Administrator", "email": email, "role": "admin",
                             "password_hash": generate_password_hash(password), "phone": "", "bio": "",
                             "is_active": True, "created_at": utcnow()})
        print("Admin account created. Log in at /login.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1", port=int(os.getenv("PORT", "5000")))
