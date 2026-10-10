"""Secure file uploads: whitelisted extensions, size limits, random stored names, image verification."""
import os
import uuid
from flask import abort, current_app
from PIL import Image
from werkzeug.utils import secure_filename
from config import Config
UPLOAD_FOLDERS = ("course_images", "notes", "materials", "assignments",
                  "submissions", "profiles", "certificates")
def _extension(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
def save_upload(file_storage, folder, allowed_extensions, max_bytes):
    """Save an uploaded file and return (stored_name, original_name).
    Raises ValueError with a user-friendly message when the file is not acceptable.
    """
    if folder not in UPLOAD_FOLDERS:
        raise ValueError("Invalid upload destination.")
    original = secure_filename(file_storage.filename or "")
    ext = _extension(original)
    if not original or ext not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"File type not allowed. Allowed types: {allowed}.")
    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise ValueError("The uploaded file is empty.")
    if size > max_bytes:
        raise ValueError(f"File is too large. Maximum size is {max_bytes // (1024 * 1024)} MB.")
    stored = f"{uuid.uuid4().hex}.{ext}"
    target_dir = os.path.join(current_app.config["UPLOAD_ROOT"], folder)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, stored)
    file_storage.save(path)
    if ext in Config.IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as img:
                img.verify() 
        except Exception:
            os.remove(path)
            raise ValueError("The uploaded image is not a valid picture.")
    return stored, original
def safe_upload_path(folder, stored_name):
    """Build an absolute path inside the uploads folder. Anything suspicious returns 404."""
    if folder not in UPLOAD_FOLDERS or not stored_name or secure_filename(stored_name) != stored_name:
        abort(404)
    return os.path.join(current_app.config["UPLOAD_ROOT"], folder, stored_name)
def remove_upload(folder, stored_name):
    if not stored_name:
        return
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_ROOT"], folder, stored_name))
    except OSError:
        pass
