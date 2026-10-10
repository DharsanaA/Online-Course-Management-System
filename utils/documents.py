"""PDF certificates (ReportLab), PDF/CSV report responses, and automatic certificate issuing."""
import csv
import io
import os
from flask import Response, current_app, send_file
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from database.mongodb import get_db
from utils.helpers import course_progress, get_settings, log_activity, notify, utcnow
NAVY = colors.HexColor("#1e3a8a")
SLATE = colors.HexColor("#334155")
GOLD = colors.HexColor("#b45309")
def draw_certificate(path, data):
    """Draw a landscape A4 certificate of completion."""
    width, height = landscape(A4)
    pdf = canvas.Canvas(path, pagesize=(width, height))
    pdf.setTitle(f"Certificate {data['certificate_id']}")
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(6)
    pdf.rect(20, 20, width - 40, height - 40)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1.5)
    pdf.rect(32, 32, width - 64, height - 64)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width / 2, height - 70, data["org_name"].upper())
    pdf.setFont("Helvetica-Bold", 34)
    pdf.drawCentredString(width / 2, height - 115, "Certificate of Completion")
    pdf.setFillColor(SLATE)
    pdf.setFont("Helvetica", 16)
    pdf.drawCentredString(width / 2, height - 155, "This certifies that")
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(width / 2, height - 198, data["student_name"])
    pdf.setFillColor(SLATE)
    pdf.setFont("Helvetica", 16)
    pdf.drawCentredString(width / 2, height - 232, "has successfully completed the course")
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawCentredString(width / 2, height - 268, data["course_title"])
    pdf.setFillColor(SLATE)
    pdf.setFont("Helvetica", 13)
    pdf.drawCentredString(width / 2, height - 300,
                          f"Instructor: {data['instructor_name']}     Completed on: {data['completion_date']}")
    pdf.setStrokeColor(SLATE)
    pdf.setLineWidth(0.8)
    pdf.line(120, 150, 340, 150)
    pdf.line(width - 340, 150, width - 120, 150)
    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(230, 132, data["instructor_name"])
    pdf.drawCentredString(230, 118, "Course Instructor")
    pdf.drawCentredString(width - 230, 132, data["signatory"])
    pdf.drawCentredString(width - 230, 118, "Authorized Signatory")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 52, f"Certificate ID: {data['certificate_id']}")
    pdf.drawRightString(width - 60, 52, "Verify at the platform's certificate verification page")
    pdf.showPage()
    pdf.save()
def next_certificate_id(db):
    """Sequential, unique ID such as OCMS-2026-00001 using an atomic counter."""
    year = utcnow().year
    counter = db.counters.find_one_and_update(
        {"_id": f"certificate_{year}"}, {"$inc": {"seq": 1}},
        upsert=True, return_document=ReturnDocument.AFTER)
    return f"OCMS-{year}-{counter['seq']:05d}"
def issue_certificate_if_eligible(student_id, course_id):
    """Issue the certificate once the student has met every requirement. Idempotent."""
    db = get_db()
    existing = db.certificates.find_one({"student_id": student_id, "course_id": course_id})
    if existing:
        return existing
    if course_progress(student_id, course_id)["status"] != "COMPLETED":
        return None
    course = db.courses.find_one({"_id": course_id})
    student = db.users.find_one({"_id": student_id})
    instructor = db.users.find_one({"_id": course.get("instructor_id")}) if course.get("instructor_id") else None
    settings = get_settings()
    completed_on = utcnow()
    cert_id = next_certificate_id(db)
    file_name = f"{cert_id}.pdf"
    path = os.path.join(current_app.config["UPLOAD_ROOT"], "certificates", file_name)
    draw_certificate(path, {
        "certificate_id": cert_id,
        "org_name": settings["certificate_org"],
        "student_name": student["name"],
        "course_title": course["title"],
        "instructor_name": instructor["name"] if instructor else "Unassigned",
        "completion_date": completed_on.strftime("%d %B %Y"),
        "signatory": settings["certificate_org"],
    })
    doc = {
        "certificate_id": cert_id, "student_id": student_id, "student_name": student["name"],
        "course_id": course_id, "course_title": course["title"],
        "instructor_id": course.get("instructor_id"),
        "instructor_name": instructor["name"] if instructor else "Unassigned",
        "completion_date": completed_on, "file_name": file_name, "issued_at": completed_on,
    }
    try:
        db.certificates.insert_one(doc)
    except DuplicateKeyError:
        os.remove(path)
        return db.certificates.find_one({"student_id": student_id, "course_id": course_id})

    db.enrollments.update_one({"student_id": student_id, "course_id": course_id},
                              {"$set": {"status": "COMPLETED", "completed_at": completed_on}})
    notify(student_id, "Certificate generated",
           f"Congratulations! Your certificate for {course['title']} is ready.",
           link="/student/certificates", kind="certificate")
    notify(student_id, "Course completed", f"You completed {course['title']}.", kind="success")
    log_activity(student_id, "student", "Course completion", course["title"])
    log_activity(student_id, "student", "Certificate generation", cert_id)
    return doc
def csv_response(filename, headers, rows):
    """CSV download built with the Python csv module. BOM keeps Excel happy with UTF-8."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
def pdf_response(filename, title, headers, rows):
    """Tabular PDF report built with ReportLab."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=28, rightMargin=28,
                            topMargin=36, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(title, styles["Title"]),
        Paragraph(f"Generated {utcnow():%d %b %Y %H:%M} UTC · {len(rows)} record(s)", styles["Normal"]),
        Spacer(1, 12),
    ]
    body = [[str(cell) for cell in row] for row in rows] or [["No records found"] + [""] * (len(headers) - 1)]
    table = Table([headers] + body, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{filename}.pdf")
