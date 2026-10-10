"""Index definitions. Created once at startup; safe to run repeatedly."""
from pymongo import ASCENDING, DESCENDING


def create_indexes(db):
    db.users.create_index("email", unique=True)
    db.users.create_index("role")

    db.courses.create_index("title")                  
    db.courses.create_index("category")              
    db.courses.create_index("difficulty")             
    db.courses.create_index("duration_weeks")         
    db.courses.create_index("tags")                   
    db.courses.create_index("instructor_id")          
    db.courses.create_index("instructor.staff_id")   
    db.courses.create_index([("status", ASCENDING), ("created_at", DESCENDING)])

    db.modules.create_index([("course_id", ASCENDING), ("order", ASCENDING)])
    db.lessons.create_index([("course_id", ASCENDING), ("order", ASCENDING)])
    db.materials.create_index("course_id")

    db.enrollments.create_index([("student_id", ASCENDING), ("course_id", ASCENDING)], unique=True)
    db.enrollments.create_index("course_id")
    db.enrollments.create_index("student_id")
    db.enrollments.create_index("instructor_id")
    db.enrollments.create_index([("enrolled_at", DESCENDING)])

    db.progress.create_index([("student_id", ASCENDING), ("lesson_id", ASCENDING)], unique=True)
    db.progress.create_index([("student_id", ASCENDING), ("course_id", ASCENDING)])

    db.quizzes.create_index("course_id")
    db.quiz_attempts.create_index([("student_id", ASCENDING), ("quiz_id", ASCENDING)])
    db.quiz_attempts.create_index([("course_id", ASCENDING), ("attempted_at", DESCENDING)])

    db.assignments.create_index("course_id")
    db.assignment_submissions.create_index([("assignment_id", ASCENDING), ("student_id", ASCENDING)], unique=True)
    db.assignment_submissions.create_index([("course_id", ASCENDING), ("status", ASCENDING)])

    db.reviews.create_index([("course_id", ASCENDING), ("student_id", ASCENDING)], unique=True)

    db.wishlists.create_index([("student_id", ASCENDING), ("course_id", ASCENDING)], unique=True)
    db.wishlists.create_index("course_id")

    db.certificates.create_index("certificate_id", unique=True)
    db.certificates.create_index([("student_id", ASCENDING), ("course_id", ASCENDING)], unique=True)

    db.notifications.create_index([("user_id", ASCENDING), ("is_read", ASCENDING), ("created_at", DESCENDING)])
    db.activity_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    db.activity_logs.create_index([("created_at", DESCENDING)])

    db.announcements.create_index([("course_id", ASCENDING), ("created_at", DESCENDING)])
