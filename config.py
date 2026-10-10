"""Application configuration. All secrets come from environment variables (.env)."""
import os
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "online_course_management")

    SECRET_KEY = os.getenv("SECRET_KEY", "")

    STAFF_SIGNUP_CODE = os.getenv("STAFF_SIGNUP_CODE", "")
    ADMIN_SIGNUP_CODE = os.getenv("ADMIN_SIGNUP_CODE", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024
    IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    IMAGE_MAX_BYTES = 3 * 1024 * 1024
    DOCUMENT_EXTENSIONS = {"pdf", "docx", "pptx", "txt", "zip", "png", "jpg", "jpeg"}
    DOCUMENT_MAX_BYTES = 20 * 1024 * 1024

    QUIZ_GRACE_SECONDS = 30
    DEFAULT_PASSING_PERCENT = 60
    PER_PAGE = 9

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY:
            raise RuntimeError("SECRET_KEY is missing. Copy .env.example to .env and set SECRET_KEY.")
        if not cls.MONGO_URI:
            raise RuntimeError("MONGO_URI is missing. Set it in your .env file.")
