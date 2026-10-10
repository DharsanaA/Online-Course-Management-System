"""MongoDB query service for the NoSQL demonstration.

Every function here runs real MongoDB work (find, aggregate, explain). Routes call these
helpers instead of filtering in Python, so the database does the searching, filtering,
sorting, pagination, projection, $lookup and $unwind.

Quick map (for the NoSQL instructor):
  catalog_count / catalog_page ... search + filter + sort + pagination + $lookup + $count + $project
  course_overview ................ $lookup (courses -> users, enrollments -> users) + projection
  course_structure ............... $unwind (course -> modules -> lessons) + $group
  enrollment_page ................ $sort + skip/limit pagination + $lookup
  explain_plan ................... index proof: IXSCAN (index used) vs COLLSCAN (full scan)
"""
import re

from bson import ObjectId

SORT_SPEC = {
    "name_asc": [("title", 1), ("_id", 1)],
    "name_desc": [("title", -1), ("_id", 1)],
    "rating_desc": [("rating", -1), ("title", 1)],
    "rating_asc": [("rating", 1), ("title", 1)],
    "newest": [("created_at", -1)],
    "oldest": [("created_at", 1)],
    "duration_asc": [("duration_weeks", 1), ("title", 1)],
    "duration_desc": [("duration_weeks", -1), ("title", 1)],
}
SORT_LABELS = {
    "name_asc": "Course name A–Z",
    "name_desc": "Course name Z–A",
    "rating_desc": "Highest rating",
    "rating_asc": "Lowest rating",
    "newest": "Newest courses",
    "oldest": "Oldest courses",
    "duration_asc": "Shortest duration",
    "duration_desc": "Longest duration",
}

DURATION_BANDS = {"short": (None, 4), "medium": (5, 8), "long": (9, None)}
DURATION_LABELS = {"short": "Up to 4 weeks", "medium": "5–8 weeks", "long": "9+ weeks"}


def _base_stages(db, f):
    """Stages shared by the count and the page query.

    1. $match      filter courses on indexed fields (status, category, level, instructor, duration, tag)
    2. $lookup     join the instructor document from users (courses -> users)
    3. $lookup     join reviews and enrollments so ratings and student counts are calculated
    4. $addFields  compute rating, review_count, students and instructor_name
    5. $match      search text and minimum rating (these need the computed fields)
    """
    match = {"status": "published"}
    if f.get("category"):
        match["category"] = f["category"]
    if f.get("difficulty"):
        match["difficulty"] = f["difficulty"]
    if f.get("instructor_id"):
        match["instructor_id"] = f["instructor_id"]
    if f.get("tag"):
        match["tags"] = f["tag"] 
    band = DURATION_BANDS.get(f.get("duration"))
    if band:
        low, high = band
        rng = {}
        if low is not None:
            rng["$gte"] = low
        if high is not None:
            rng["$lte"] = high
        match["duration_weeks"] = rng

    stages = [
        {"$match": match},
        {"$lookup": {"from": "users", "localField": "instructor_id", "foreignField": "_id",
                     "as": "instructor_doc"}},
        {"$lookup": {"from": "reviews", "localField": "_id", "foreignField": "course_id",
                     "as": "review_docs"}},
        {"$lookup": {"from": "enrollments", "localField": "_id", "foreignField": "course_id",
                     "as": "enrollment_docs"}},
        {"$addFields": {
            "instructor_name": {"$arrayElemAt": ["$instructor_doc.name", 0]},
            "review_count": {"$size": "$review_docs"},
            "students": {"$size": "$enrollment_docs"},
           
            "rating": {"$cond": [{"$gt": [{"$size": "$review_docs"}, 0]},
                                 {"$avg": "$review_docs.rating"}, 0]},
        }},
    ]

    if f.get("q"):
        pattern = {"$regex": re.escape(f["q"]), "$options": "i"}
  
        stages.append({"$match": {"$or": [{"title": pattern}, {"description": pattern},
                                          {"category": pattern}, {"instructor_name": pattern}]}})
    if f.get("min_rating"):
        stages.append({"$match": {"rating": {"$gte": f["min_rating"]}}})
    return stages


def catalog_count(db, f):
    """Total matching courses. Uses $count so MongoDB counts without returning documents."""
    rows = list(db.courses.aggregate(_base_stages(db, f) + [{"$count": "total"}]))
    return rows[0]["total"] if rows else 0


def catalog_page(db, f, sort_key, skip, limit):
    """One page of course cards.

    $sort   order by the chosen option (rating, name, date or duration)
    $skip / $limit   pagination inside MongoDB
    $project only the fields the course card needs (projection)
    """
    sort_spec = SORT_SPEC.get(sort_key, SORT_SPEC["newest"])
    stages = _base_stages(db, f) + [
        {"$sort": dict(sort_spec)},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {
            "title": 1, "description": 1, "category": 1, "difficulty": 1, "duration_weeks": 1,
            "tags": 1, "image": 1, "instructor_id": 1, "instructor_name": 1, "created_at": 1,
            "rating": 1, "review_count": 1, "students": 1,
        }},
    ]
    return list(db.courses.aggregate(stages))


def catalog_filter_options(db):
    """Distinct values for the filter dropdowns: $match then distinct on published courses."""
    published = {"status": "published"}
    return {
        "categories": sorted(c for c in db.courses.distinct("category", published) if c),
        "instructor_ids": db.courses.distinct("instructor_id", published),
    }


def course_overview(db):
    """Course name, instructor, number of students and student details.

    Pipeline:
      courses -> $lookup users (instructor)
      courses -> $lookup enrollments -> $unwind enrollments
      each enrollment -> $lookup users (student) -> $unwind students
      $group back to one row per course, $project only the fields the page needs
    """
    rows = db.courses.aggregate([
        {"$sort": {"title": 1}},
        {"$lookup": {"from": "users", "localField": "instructor_id", "foreignField": "_id",
                     "as": "instructor"}},
        {"$lookup": {"from": "enrollments", "localField": "_id", "foreignField": "course_id",
                     "as": "enrollments"}},
        {"$unwind": {"path": "$enrollments", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {"from": "users", "localField": "enrollments.student_id", "foreignField": "_id",
                     "as": "student"}},
        {"$unwind": {"path": "$student", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$_id",
            "title": {"$first": "$title"},
            "status": {"$first": "$status"},
            "instructor": {"$first": "$instructor"},
        
            "students": {"$push": {"name": "$student.name", "email": "$student.email"}},
            "enrollment_ids": {"$addToSet": "$enrollments._id"},
        }},
        {"$project": {
            "title": 1, "status": 1,
            "instructor_name": {"$arrayElemAt": ["$instructor.name", 0]},
            "number_of_students": {"$size": "$enrollment_ids"},
            "students": 1,
        }},
        {"$sort": {"title": 1}},
    ])
    result = []
    for row in rows:
        row["students"] = [s for s in row["students"] if s.get("email")]
        row["number_of_students"] = len(row["students"]) if row["students"] else 0
        result.append(row)
    return result

def course_structure(db):
    """Modules and lesson counts per course, computed by unwinding course -> modules -> lessons.

    $lookup modules, $unwind modules, $lookup lessons, $unwind lessons, then $group back per course.
    preserveNullAndEmptyArrays keeps courses that have no modules or lessons yet.
    """
    rows = db.courses.aggregate([
        {"$lookup": {"from": "modules", "localField": "_id", "foreignField": "course_id",
                     "as": "modules"}},
        {"$unwind": {"path": "$modules", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {"from": "lessons", "localField": "modules._id", "foreignField": "module_id",
                     "as": "lessons"}},
        {"$unwind": {"path": "$lessons", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$_id",
            "title": {"$first": "$title"},
            "module_ids": {"$addToSet": "$modules._id"},
            "lesson_ids": {"$addToSet": "$lessons._id"},
        }},
        {"$sort": {"title": 1}},
    ])
    result = []
    for row in rows:
        result.append({
            "title": row["title"],
            "modules": len([m for m in row["module_ids"] if m is not None]),
            "lessons": len([l for l in row["lesson_ids"] if l is not None]),
        })
    return result



def enrollment_count(db):
    return db.enrollments.count_documents({})


def enrollment_page(db, skip, limit):
    """Enrollments newest first, with course title and student name joined in MongoDB."""
    return list(db.enrollments.aggregate([
        {"$sort": {"enrolled_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$lookup": {"from": "courses", "localField": "course_id", "foreignField": "_id",
                     "as": "course"}},
        {"$lookup": {"from": "users", "localField": "student_id", "foreignField": "_id",
                     "as": "student"}},
        {"$project": {
            "enrolled_at": 1,
            "course_title": {"$arrayElemAt": ["$course.title", 0]},
            "student_name": {"$arrayElemAt": ["$student.name", 0]},
            "student_email": {"$arrayElemAt": ["$student.email", 0]},
        }},
    ]))


def _stages_in_plan(plan):
    stages = []
    node = plan.get("queryPlanner", {}).get("winningPlan", {})
    while node:
        stages.append(node.get("stage", "?"))
        node = node.get("inputStage")
    return stages


def explain_plan(db, collection, filter_, sort=None):
    """Ask MongoDB how it runs a query. IXSCAN means an index was used; COLLSCAN means a full scan."""
    try:
        cursor = db[collection].find(filter_)
        if sort:
            cursor = cursor.sort(sort)
        return {"stages": _stages_in_plan(cursor.explain())}
    except Exception:  
        return {"stages": ["unavailable on this server"]}


def index_demo(db):
    """Three queries with the plan MongoDB chooses for each one."""
    return [
        {"label": "Courses in one category (uses index on category)",
         "query": '{"category": "…"}', "plan": explain_plan(db, "courses", {"category": "Programming"})},
        {"label": "Published courses, newest first (uses compound index status + created_at)",
         "query": '{"status": "published"} sort created_at desc',
         "plan": explain_plan(db, "courses", {"status": "published"}, [("created_at", -1)])},
        {"label": "Title contains a word (no index can help a 'contains' search)",
         "query": '{"title": {"$regex": "python"}}',
         "plan": explain_plan(db, "courses", {"title": {"$regex": "python", "$options": "i"}})},
    ]


def to_object_id(value):
    """Convert a string to ObjectId, or None when the string is not a valid id."""
    try:
        return ObjectId(value)
    except Exception:
        return None
