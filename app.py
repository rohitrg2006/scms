from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, Response, send_file)
import json, time, queue, threading, os, csv, io
from datetime import date, datetime
from database import *
from mailer import send_credentials_email, test_smtp_connection, send_announcement_email
from face_recognition_module import (encode_face_from_b64, match_face_from_b64,
                                     check_available as fr_check_available,
                                     _encoding_to_json)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_me_in_production_2024")

if __name__ == "__main__" or os.environ.get("FLASK_RUN"):
    connect_db()

# ── SSE ───────────────────────────────────────────────────────────────────────
listeners = []
listeners_lock = threading.Lock()

def push_event(data: dict):
    msg = f"data: {json.dumps(data)}\n\n"
    with listeners_lock:
        dead = []
        for q in listeners:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            listeners.remove(q)

# ── AUTH DECORATORS ───────────────────────────────────────────────────────────
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                role = session.get("role")
                if role == "student":
                    return redirect(url_for("student_portal"))
                elif role == "parent":
                    return redirect(url_for("parent_portal"))
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator

STAFF_ROLES = ("admin", "hod", "faculty", "teacher")

def subject_options_for_current_user(dept=None, sem=None):
    if session.get("role") == "teacher":
        subjects = get_teacher_subjects(session.get("user_id"))
        if dept:
            subjects = [s for s in subjects if s["department"] == dept]
        if sem:
            subjects = [s for s in subjects if int(s["semester"]) == int(sem)]
        return subjects
    return get_subjects(dept, int(sem) if sem else None)

def can_use_subject(subject_id):
    if session.get("role") != "teacher":
        return True
    if not subject_id:
        return False
    return teacher_can_use_subject(session.get("user_id"), int(subject_id))

# ── LOGIN / LOGOUT ────────────────────────────────────────────────────────────
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        role_sel = request.form.get("role","").strip()   # role hint from UI
        user = login_user(username, password)
        if user:
            # Validate role matches selection if provided
            if role_sel and user["role"] != role_sel:
                return render_template("login.html", error="Role mismatch. Please select your correct role.")
            session["user_id"]  = user["id"]
            session["role"]     = user["role"]
            session["username"] = user["username"]
            role = user["role"]
            if role in STAFF_ROLES:
                return redirect(url_for("dashboard"))
            elif role == "student":
                return redirect(url_for("student_portal"))
            elif role == "parent":
                return redirect(url_for("parent_portal"))
        return render_template("login.html", error="Invalid credentials. Please try again.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── DASHBOARD ─────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
@roles_required(*STAFF_ROLES)
def dashboard():
    dept = request.args.get("dept","")
    stats   = get_dashboard_stats(dept if dept else None)
    depts   = get_departments()
    classes = get_classes()
    return render_template("dashboard.html", stats=stats, depts=depts,
                           classes=classes, dept_filter=dept,
                           username=session.get("username"), role=session.get("role"))

@app.route("/api/stats")
@login_required
def api_stats():
    dept = request.args.get("dept","")
    return jsonify(get_dashboard_stats(dept if dept else None))


@app.route("/teachers", methods=["GET","POST"])
@login_required
@roles_required("admin","hod")
def teachers():
    if request.method == "POST":
        full_name   = request.form.get("full_name","").strip()
        employee_id = request.form.get("employee_id","").strip().upper()
        username    = request.form.get("username","").strip()
        password    = request.form.get("password","").strip() or "teacher123"
        email       = request.form.get("email","").strip()
        phone       = request.form.get("phone","").strip()
        department  = request.form.get("department","").strip().upper()
        designation = request.form.get("designation","Teacher").strip() or "Teacher"
        ok, error = add_teacher(full_name, employee_id, username, password,
                                email, phone, department, designation)
        if ok:
            return redirect(url_for("teachers", added=1))
        return redirect(url_for("teachers", error=error or "Could not add teacher"))

    return render_template("teachers.html", teachers=get_teachers(),
                           error=request.args.get("error",""), added=request.args.get("added",""),
                           username=session.get("username"), role=session.get("role"))

@app.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
@login_required
@roles_required("admin","hod")
def delete_teacher_route(teacher_id):
    if teacher_id == session.get("user_id"):
        return redirect(url_for("teachers", error="You cannot delete your own account"))
    delete_teacher(teacher_id)
    return redirect(url_for("teachers"))

@app.route("/subjects", methods=["GET","POST"])
@login_required
@roles_required("admin","hod")
def subjects():
    if request.method == "POST":
        code       = request.form.get("code","").strip().upper()
        name       = request.form.get("name","").strip()
        department = request.form.get("department","").strip().upper()
        semester   = int(request.form.get("semester",1) or 1)
        credits    = int(request.form.get("credits",3) or 3)
        teacher_id = int(request.form.get("teacher_id",0) or 0) or None
        ok = add_subject(code, name, department, semester, credits, teacher_id=teacher_id)
        return redirect(url_for("subjects", added=1 if ok else "", error="" if ok else "Subject code already exists"))

    return render_template("subjects.html", subjects=get_subjects(), teachers=get_teachers(),
                           depts=get_departments(), error=request.args.get("error",""),
                           added=request.args.get("added",""),
                           username=session.get("username"), role=session.get("role"))

@app.route("/subjects/<int:subject_id>/assign", methods=["POST"])
@login_required
@roles_required("admin","hod")
def assign_subject_route(subject_id):
    teacher_id = request.form.get("teacher_id",0)
    ok = assign_subject_to_teacher(subject_id, teacher_id)
    return redirect(url_for("subjects", error="" if ok else "Selected teacher was not found"))

@app.route("/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required
@roles_required("admin","hod")
def delete_subject_page_route(subject_id):
    delete_subject(subject_id)
    return redirect(url_for("subjects"))

# ── STUDENTS ──────────────────────────────────────────────────────────────────
@app.route("/students")
@login_required
@roles_required(*STAFF_ROLES)
def students():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    sec  = request.args.get("sec","")
    all_students = get_students(dept or None, sem or None, sec or None)
    depts = get_departments()
    return render_template("students.html", students=all_students, depts=depts,
                           dept_filter=dept, sem_filter=sem, sec_filter=sec,
                           username=session.get("username"), role=session.get("role"))

@app.route("/students/add", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def add_student_route():
    name         = request.form.get("name","").strip()
    department   = request.form.get("department","").strip()
    semester     = request.form.get("semester","1").strip()
    section      = request.form.get("section","A").strip().upper()
    email        = request.form.get("email","").strip()
    phone        = request.form.get("phone","").strip()
    parent_email = request.form.get("parent_email","").strip()
    parent_phone = request.form.get("parent_phone","").strip()
    usn          = request.form.get("usn","").strip()
    send_email_flag = request.form.get("send_email","0") == "1"
    if name and department:
        sid, username, password, usn_out = add_student(name, department, int(semester), section,
                                                        email, phone, parent_email, parent_phone, usn)
        if sid:
            push_event({"type":"student_added","name":name,"dept":department,"sem":semester})
            email_status = email_error = None
            if send_email_flag and email:
                cfg  = get_email_config()
                base = request.host_url.rstrip("/")
                ok, err = send_credentials_email(email, name, f"{department} Sem {semester}", username, password, cfg,
                                                  qr_path=f"qrcodes/student_{sid}.png", portal_url=base)
                email_status = "sent" if ok else "failed"
                email_error  = err
                log_email_sent(sid, email, email_status, err)
            return jsonify({"success":True,"student_id":sid,"username":username,
                            "password":password,"usn":usn_out,
                            "email_status":email_status,"email_error":email_error})
    return jsonify({"success":False,"error":"Missing fields"})

@app.route("/students/delete/<int:sid>", methods=["POST"])
@login_required
@roles_required("admin","hod")
def delete_student_route(sid):
    delete_student(sid)
    push_event({"type":"student_deleted","student_id":sid})
    return jsonify({"success":True})

@app.route("/students/<int:sid>")
@login_required
@roles_required(*STAFF_ROLES)
def student_detail(sid):
    s = get_student_by_id(sid)
    if not s:
        return redirect(url_for("students"))
    history  = get_student_attendance_history(sid)
    report   = get_attendance_report(sid)
    rep      = report[0] if report else {}
    subj_att = get_subject_attendance_summary(sid)
    marks    = get_student_marks(sid)
    fees     = get_student_fee_status(sid)
    parents  = get_parents_for_student(sid)
    from datetime import timedelta
    cal = {}
    today = date.today()
    for i in range(29,-1,-1):
        d = (today - timedelta(days=i)).isoformat()
        cal[d] = "none"
    for h in history:
        if h["date"] in cal:
            cal[h["date"]] = h["status"]
    streak = 0
    for d in sorted(cal.keys(), reverse=True):
        if cal[d] == "Present":
            streak += 1
        elif cal[d] == "Absent":
            break
    return render_template("student_detail.html", student=s, history=history,
                           report=rep, cal=cal, streak=streak, subj_att=subj_att,
                           marks=marks, fees=fees, parents=parents,
                           username=session.get("username"), role=session.get("role"))

@app.route("/students/<int:sid>/update", methods=["POST"])
@login_required
@roles_required("admin","hod")
def update_student_route(sid):
    d = request.get_json()
    update_student(sid, **d)
    return jsonify({"success":True})

@app.route("/students/qr/<int:sid>")
@login_required
def get_qr(sid):
    path = f"qrcodes/student_{sid}.png"
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "QR not found", 404

@app.route("/students/import", methods=["GET","POST"])
@login_required
@roles_required(*STAFF_ROLES)
def import_students():
    if request.method == "GET":
        depts = get_departments()
        return render_template("import_students.html", depts=depts,
                               username=session.get("username"), role=session.get("role"))
    file = request.files.get("csv_file")
    if not file:
        return jsonify({"success":False,"error":"No file uploaded"})
    stream  = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader  = csv.DictReader(stream)
    added, skipped = 0, 0
    results = []
    for row in reader:
        name   = (row.get("name","") or row.get("Name","")).strip()
        dept   = (row.get("department","") or row.get("dept","")).strip()
        sem    = (row.get("semester","1") or "1").strip()
        sec    = (row.get("section","A") or "A").strip().upper()
        email  = (row.get("email","") or "").strip()
        phone  = (row.get("phone","") or "").strip()
        pe     = (row.get("parent_email","") or "").strip()
        pp     = (row.get("parent_phone","") or "").strip()
        usn    = (row.get("usn","") or "").strip()
        if name and dept:
            sid, uname, pwd, usn_out = add_student(name, dept, int(sem), sec, email, phone, pe, pp, usn)
            if sid:
                added += 1
                results.append({"name":name,"dept":dept,"semester":sem,"username":uname,"password":pwd,"usn":usn_out})
                push_event({"type":"student_added","name":name,"dept":dept})
            else:
                skipped += 1
        else:
            skipped += 1
    return jsonify({"success":True,"added":added,"skipped":skipped,"results":results})

# ── ATTENDANCE ────────────────────────────────────────────────────────────────
@app.route("/attendance")
@login_required
@roles_required(*STAFF_ROLES)
def attendance():
    dept       = request.args.get("dept","")
    sem        = request.args.get("sem","")
    sec        = request.args.get("sec","")
    subject_id = request.args.get("subject_id","")
    subjects   = subject_options_for_current_user(dept or None, sem or None)
    if session.get("role") == "teacher":
        if not subjects:
            depts  = get_departments()
            today  = date.today().strftime("%A, %d %B %Y")
            return render_template("attendance.html", today_data=[], depts=depts,
                           subjects=[], report={"total":0,"present":0,"absent":0,"percentage":0},
                                   today=today, today_iso=date.today().isoformat(),
                                   dept_filter=dept, sem_filter=sem, sec_filter=sec,
                                   subject_id=subject_id, no_assigned_subjects=True,
                                   username=session.get("username"), role=session.get("role"))
        if not subject_id or not can_use_subject(subject_id):
            subject_id = str(subjects[0]["id"])
            dept = subjects[0]["department"]
            sem = str(subjects[0]["semester"])
    today_data = get_today_attendance(dept or None, sem or None, sec or None,
                                      int(subject_id) if subject_id else None)
    depts    = get_departments()
    report   = get_attendance_report(dept=dept or None, semester=int(sem) if sem else None)
    today    = date.today().strftime("%A, %d %B %Y")
    return render_template("attendance.html", today_data=today_data, depts=depts,
                           subjects=subjects, report=report, today=today,
                           today_iso=date.today().isoformat(),
                           dept_filter=dept, sem_filter=sem, sec_filter=sec,
                           subject_id=subject_id,
                           username=session.get("username"), role=session.get("role"))

@app.route("/attendance/mark", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def mark_attendance_route():
    data       = request.get_json()
    student_id = data.get("student_id")
    status     = data.get("status","Present")
    subject_id = data.get("subject_id") or None
    period_no  = int(data.get("period_no",1))
    att_date   = data.get("date") or None
    if not can_use_subject(subject_id):
        return jsonify({"success":False,"error":"Teacher is not assigned to this subject"}), 403
    if student_id:
        mark_attendance(student_id, status, marked_by=session.get("username","manual"),
                        subject_id=subject_id, period_no=period_no, att_date=att_date)
        s = get_student_by_id(student_id)
        if s:
            push_event({"type":"attendance_marked","student_id":student_id,
                        "name":s["name"],"dept":s["department"],"sem":s["semester"],
                        "status":status,"time":datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success":True})
    return jsonify({"success":False})

@app.route("/attendance/mark-all", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def mark_all_present():
    data       = request.get_json()
    dept       = data.get("dept","")
    sem        = data.get("sem","")
    sec        = data.get("sec","")
    subject_id = data.get("subject_id") or None
    period_no  = int(data.get("period_no",1))
    if not can_use_subject(subject_id):
        return jsonify({"success":False,"error":"Teacher is not assigned to this subject"}), 403
    students_list = get_students(dept or None, sem or None, sec or None)
    for s in students_list:
        mark_attendance(s["id"],"Present",marked_by=session.get("username","manual"),
                        subject_id=subject_id, period_no=period_no)
    push_event({"type":"bulk_attendance","dept":dept,"count":len(students_list)})
    return jsonify({"success":True,"count":len(students_list)})

@app.route("/api/today-attendance")
@login_required
def api_today_attendance():
    dept       = request.args.get("dept","")
    sem        = request.args.get("sem","")
    sec        = request.args.get("sec","")
    subject_id = request.args.get("subject_id","")
    data = get_today_attendance(dept or None, sem or None, sec or None,
                                int(subject_id) if subject_id else None)
    return jsonify(data)

# ── TIMETABLE ─────────────────────────────────────────────────────────────────
@app.route("/timetable")
@login_required
@roles_required(*STAFF_ROLES)
def timetable():
    dept = request.args.get("dept","CSE")
    sem  = request.args.get("sem","1")
    sec  = request.args.get("sec","A")
    tt   = get_timetable(dept, int(sem), sec)
    depts   = get_departments()
    subjects= subject_options_for_current_user(dept, int(sem))
    days    = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
    periods = list(range(1,9))
    grid = {d:{p:None for p in periods} for d in days}
    for e in tt:
        if e["day"] in grid and e["period"] in grid[e["day"]]:
            grid[e["day"]][e["period"]] = e
    return render_template("timetable.html", grid=grid, days=days, periods=periods,
                           depts=depts, subjects=subjects, dept=dept, sem=sem, sec=sec,
                           username=session.get("username"), role=session.get("role"))

@app.route("/timetable/add", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def add_timetable():
    d = request.get_json()
    if d.get("subject_id") and not can_use_subject(d.get("subject_id")):
        return jsonify({"success":False,"error":"Teacher is not assigned to this subject"}), 403
    add_timetable_entry(d["dept"], int(d["sem"]), d["sec"], d["day"], int(d["period"]),
                        d["subject"], d["faculty"], d["time_start"], d["time_end"],
                        d.get("subject_id"))
    return jsonify({"success":True})

@app.route("/timetable/delete/<int:eid>", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def delete_timetable(eid):
    delete_timetable_entry(eid)
    return jsonify({"success":True})

# ── EXAMS & MARKS ─────────────────────────────────────────────────────────────
@app.route("/exams")
@login_required
@roles_required(*STAFF_ROLES)
def exams():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    exams_list = get_exams(dept or None, int(sem) if sem else None)
    depts    = get_departments()
    subjects = subject_options_for_current_user(dept or None, int(sem) if sem else None)
    return render_template("exams.html", exams=exams_list, depts=depts,
                           subjects=subjects, dept_filter=dept, sem_filter=sem,
                           username=session.get("username"), role=session.get("role"))

@app.route("/exams/add", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def add_exam_route():
    d = request.get_json()
    if not can_use_subject(d.get("subject_id")):
        return jsonify({"success":False,"error":"Teacher is not assigned to this subject"}), 403
    eid = add_exam(d["title"], d["exam_type"], d["dept"], int(d["sem"]),
                   int(d["subject_id"]), int(d["max_marks"]), d["exam_date"])
    return jsonify({"success":True,"exam_id":eid})

@app.route("/exams/delete/<int:eid>", methods=["POST"])
@login_required
@roles_required("admin","hod")
def delete_exam_route(eid):
    delete_exam(eid)
    return jsonify({"success":True})

@app.route("/exams/<int:eid>/marks")
@login_required
@roles_required(*STAFF_ROLES)
def exam_marks(eid):
    exam_list = get_exams()
    exam = next((e for e in exam_list if e["id"]==eid), None)
    if not exam:
        return redirect(url_for("exams"))
    existing = {m["student_id"]: m for m in get_marks_for_exam(eid)}
    students = get_students(exam["department"], exam["semester"])
    return render_template("exam_marks.html", exam=exam, students=students,
                           existing=existing,
                           username=session.get("username"), role=session.get("role"))

@app.route("/exams/<int:eid>/marks/save", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def save_marks(eid):
    data = request.get_json()  # [{student_id, marks, grade, remarks}]
    for row in data:
        upsert_mark(row["student_id"], eid, float(row.get("marks",0)),
                    row.get("grade",""), row.get("remarks",""),
                    session.get("username",""))
    return jsonify({"success":True,"saved":len(data)})

@app.route("/api/exam/<int:eid>/marks")
@login_required
def api_exam_marks(eid):
    return jsonify(get_marks_for_exam(eid))

# ── FEE TRACKING ─────────────────────────────────────────────────────────────
@app.route("/fees")
@login_required
@roles_required(*STAFF_ROLES)
def fees():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    structures = get_fee_structure(dept or None, int(sem) if sem else None)
    summary    = get_fee_summary(dept or None, int(sem) if sem else None)
    defaulters = get_fee_defaulters(dept or None, int(sem) if sem else None)
    depts = get_departments()
    return render_template("fees.html", structures=structures, summary=summary,
                           defaulters=defaulters, depts=depts,
                           dept_filter=dept, sem_filter=sem,
                           username=session.get("username"), role=session.get("role"))

@app.route("/fees/structure/add", methods=["POST"])
@login_required
@roles_required("admin","hod")
def add_fee_struct():
    d = request.get_json()
    add_fee_structure(d["dept"], int(d["sem"]), d["fee_type"], d["label"],
                      float(d["amount"]), d["due_date"], d.get("academic_year",""))
    return jsonify({"success":True})

@app.route("/fees/structure/delete/<int:fsid>", methods=["POST"])
@login_required
@roles_required("admin","hod")
def del_fee_struct(fsid):
    delete_fee_structure(fsid)
    return jsonify({"success":True})

@app.route("/fees/student/<int:sid>")
@login_required
@roles_required(*STAFF_ROLES)
def student_fees(sid):
    s    = get_student_by_id(sid)
    fees = get_student_fee_status(sid)
    return render_template("student_fees.html", student=s, fees=fees,
                           username=session.get("username"), role=session.get("role"))

@app.route("/fees/pay", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def record_payment():
    d = request.get_json()
    ok = record_fee_payment(int(d["student_id"]), int(d["fee_struct_id"]),
                            float(d["amount_paid"]), d["payment_date"],
                            d["payment_mode"], d.get("receipt_no",""),
                            d.get("status","paid"), d.get("remarks",""),
                            session.get("username",""))
    return jsonify({"success":ok})

@app.route("/fees/defaulters/email", methods=["POST"])
@login_required
@roles_required("admin","hod")
def email_defaulters():
    d    = request.get_json() or {}
    dept = d.get("dept","")
    sem  = d.get("sem","")
    cfg  = get_email_config()
    defaulters = get_fee_defaulters(dept or None, int(sem) if sem else None)
    sent = 0
    for s in defaulters:
        if s.get("email"):
            ok, _ = send_announcement_email(
                s["email"], s["name"],
                "Fee Payment Reminder",
                f"Dear {s['name']}, your outstanding fee balance is ₹{s['balance']:,.2f}. "
                f"Please clear it at the earliest to avoid late fees.",
                cfg
            )
            if ok:
                sent += 1
    return jsonify({"success":True,"sent":sent})

# ── PARENTS PORTAL ────────────────────────────────────────────────────────────
@app.route("/parent")
@login_required
@roles_required("parent")
def parent_portal():
    parent = get_parent_by_user(session["user_id"])
    if not parent:
        return "Parent account not linked to a student.", 404
    student    = get_student_by_id(parent["student_id"])
    att_report = get_attendance_report(student["id"])
    att_rep    = att_report[0] if att_report else {}
    subj_att   = get_subject_attendance_summary(student["id"])
    marks      = get_student_marks(student["id"])
    fees       = get_student_fee_status(student["id"])
    anns       = get_announcements("all", student["department"])
    return render_template("parent_portal.html", parent=parent, student=student,
                           att_rep=att_rep, subj_att=subj_att,
                           marks=marks, fees=fees, announcements=anns,
                           username=session.get("username"), role=session.get("role"))

@app.route("/students/<int:sid>/parents/add", methods=["POST"])
@login_required
@roles_required("admin","hod")
def add_parent_route(sid):
    d = request.get_json()
    uid, uname, pwd = add_parent(d["name"], d["email"], d["phone"], sid)
    if uid:
        # Optionally email credentials
        cfg = get_email_config()
        if cfg.get("enabled") and d.get("email"):
            send_announcement_email(d["email"], d["name"],
                                    "Parent Portal Access",
                                    f"Your login credentials: Username: {uname}  Password: {pwd}",
                                    cfg)
        return jsonify({"success":True,"username":uname,"password":pwd})
    return jsonify({"success":False,"error":"Could not create parent account"})

@app.route("/students/<int:sid>/parents/delete/<int:pid>", methods=["POST"])
@login_required
@roles_required("admin","hod")
def del_parent_route(sid, pid):
    delete_parent(pid)
    return jsonify({"success":True})

# ── STUDENT PORTAL ────────────────────────────────────────────────────────────
@app.route("/portal")
@login_required
@roles_required("student")
def student_portal():
    student  = get_student_by_user(session["user_id"])
    if not student:
        return "Student not found", 404
    sem_filter = request.args.get("sem", str(student["semester"]))
    report   = get_attendance_report(student["id"])
    subj_att = get_subject_attendance_summary(student["id"], int(sem_filter))
    history  = get_student_attendance_history(student["id"])
    tt       = get_timetable(student["department"], int(sem_filter), student["section"])
    marks    = get_student_marks(student["id"], int(sem_filter))
    fees     = get_student_fee_status(student["id"])
    anns     = get_announcements("student", student["department"])
    days     = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
    periods  = list(range(1,9))
    grid = {d:{p:None for p in periods} for d in days}
    for e in tt:
        if e["day"] in grid:
            grid[e["day"]][e["period"]] = e
    today_day = datetime.now().strftime("%A")
    return render_template("student_portal.html", student=student,
                           report=report[0] if report else {},
                           subj_att=subj_att, history=history,
                           grid=grid, days=days, periods=periods, today_day=today_day,
                           marks=marks, fees=fees, announcements=anns,
                           sem_filter=int(sem_filter),
                           username=session.get("username"), role=session.get("role"))

@app.route("/portal/change-password", methods=["POST"])
@login_required
def change_pw_portal():
    d  = request.get_json()
    ok = change_password(session["user_id"], d.get("old_pw"), d.get("new_pw"))
    return jsonify({"success":ok,"error":"Current password is incorrect" if not ok else ""})

# ── REPORTS ───────────────────────────────────────────────────────────────────
@app.route("/reports")
@login_required
@roles_required(*STAFF_ROLES)
def reports():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    report   = get_attendance_report(dept=dept or None, semester=int(sem) if sem else None)
    depts    = get_departments()
    return render_template("reports.html", report=report, depts=depts,
                           dept_filter=dept, sem_filter=sem,
                           username=session.get("username"), role=session.get("role"))

# ── ANNOUNCEMENTS ─────────────────────────────────────────────────────────────
@app.route("/announcements")
@login_required
def announcements_page():
    role = session.get("role","all")
    anns = get_announcements(role)
    depts = get_departments()
    return render_template("announcements.html", announcements=anns, depts=depts,
                           username=session.get("username"), role=role)

@app.route("/announcements/add", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def add_announcement_route():
    d      = request.get_json()
    title  = d.get("title","").strip()
    body   = d.get("body","").strip()
    target = d.get("target","all")
    dept   = d.get("dept","")
    sem    = int(d.get("sem",0))
    pinned = int(d.get("pinned",0))
    aid    = add_announcement(title, body, session.get("username"), target, dept, sem, pinned)
    push_event({"type":"new_announcement","title":title,"id":aid})

    # Email recipients in background
    ann_obj = {"id":aid,"title":title,"body":body,"target":target,"department":dept,"semester":sem}
    def _send_emails():
        cfg = get_email_config()
        if not cfg.get("enabled"):
            return
        recipients = get_announcement_recipients(ann_obj)
        for email, name in recipients:
            send_announcement_email(email, name, title, body, cfg)
    threading.Thread(target=_send_emails, daemon=True).start()
    return jsonify({"success":True,"id":aid})

@app.route("/announcements/delete/<int:aid>", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def delete_ann(aid):
    delete_announcement(aid)
    return jsonify({"success":True})

@app.route("/announcements/pin/<int:aid>", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def pin_ann(aid):
    toggle_pin(aid)
    return jsonify({"success":True})

# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
@app.route("/api/notifications")
@login_required
def api_notifications():
    uid  = session["user_id"]
    data = get_notifications(uid)
    cnt  = unread_count(uid)
    return jsonify({"notifications":data,"unread":cnt})

@app.route("/api/notifications/read", methods=["POST"])
@login_required
def read_notifications():
    mark_notifications_read(session["user_id"])
    return jsonify({"success":True})

# ── SETTINGS ──────────────────────────────────────────────────────────────────
@app.route("/settings")
@login_required
def settings():
    user  = get_user_by_id(session["user_id"])
    subjs = get_subjects()
    depts = get_departments()
    teachers = get_teachers()
    return render_template("settings.html", user=user, subjects=subjs, depts=depts, teachers=teachers,
                           username=session.get("username"), role=session.get("role"))

@app.route("/settings/change-password", methods=["POST"])
@login_required
def change_pw():
    d  = request.get_json()
    ok = change_password(session["user_id"], d.get("old_pw"), d.get("new_pw"))
    return jsonify({"success":ok,"error":"Wrong current password" if not ok else ""})

@app.route("/settings/subjects/add", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def add_subject_route():
    d = request.get_json()
    ok = add_subject(d.get("code",""), d.get("name",""), d.get("department",""),
                     int(d.get("semester",1)), int(d.get("credits",3)), d.get("faculty",""),
                     int(d.get("teacher_id",0) or 0) or None)
    return jsonify({"success":ok})

@app.route("/settings/subjects/delete/<int:sid>", methods=["POST"])
@login_required
@roles_required("admin","hod")
def del_subject_route(sid):
    delete_subject(sid)
    return jsonify({"success":True})

@app.route("/settings/departments/add", methods=["POST"])
@login_required
@roles_required("admin")
def add_dept_route():
    d = request.get_json()
    add_department(d.get("name",""), d.get("code",""), d.get("hod_name",""))
    return jsonify({"success":True})

@app.route("/settings/departments/delete/<int:did>", methods=["POST"])
@login_required
@roles_required("admin")
def del_dept_route(did):
    delete_department(did)
    return jsonify({"success":True})

# ── EMAIL SETTINGS ────────────────────────────────────────────────────────────
@app.route("/settings/email", methods=["GET"])
@login_required
@roles_required(*STAFF_ROLES)
def email_settings():
    cfg = get_email_config()
    log = get_email_log()
    return render_template("email_settings.html", cfg=cfg, log=log,
                           username=session.get("username"), role=session.get("role"))

@app.route("/settings/email/save", methods=["POST"])
@login_required
@roles_required("admin","hod")
def save_email_cfg():
    d = request.get_json()
    save_email_config(d.get("smtp_host",""), d.get("smtp_port",587),
                      d.get("smtp_user",""), d.get("smtp_pass",""),
                      d.get("sender_name","EduTrack Pro"), int(d.get("enabled",0)))
    return jsonify({"success":True})

@app.route("/settings/email/test", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def test_email():
    d   = request.get_json()
    cfg = {**d, "enabled":1}
    ok, msg = test_smtp_connection(cfg)
    return jsonify({"success":ok,"message":msg})

@app.route("/email/send/<int:sid>", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def send_one_email(sid):
    s = get_student_with_user(sid)
    if not s:
        return jsonify({"success":False,"error":"Student not found"})
    if not s.get("email"):
        return jsonify({"success":False,"error":"Student has no email address"})
    cfg  = get_email_config()
    base = request.host_url.rstrip("/")
    ok, err = send_credentials_email(s["email"], s["name"],
                                     f"{s['department']} Sem {s['semester']}",
                                     s["username"], s["raw_password"], cfg,
                                     qr_path=f"qrcodes/student_{sid}.png", portal_url=base)
    status = "sent" if ok else "failed"
    log_email_sent(sid, s["email"], status, err)
    return jsonify({"success":ok,"error":err})

@app.route("/email/send-bulk", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def send_bulk_emails():
    data     = request.get_json() or {}
    dept     = data.get("dept","")
    sem      = data.get("sem","")
    cfg      = get_email_config()
    base     = request.host_url.rstrip("/")
    students = get_all_students_with_credentials(dept or None, int(sem) if sem else None)

    def do_send():
        for s in students:
            ok, err = send_credentials_email(s["email"], s["name"],
                                             f"{s['department']} Sem {s['semester']}",
                                             s["username"], s["raw_password"], cfg,
                                             qr_path=f"qrcodes/student_{s['id']}.png",
                                             portal_url=base)
            log_email_sent(s["id"], s["email"], "sent" if ok else "failed", err)
            push_event({"type":"email_sent","name":s["name"],"status":"sent" if ok else "failed"})

    threading.Thread(target=do_send, daemon=True).start()
    return jsonify({"success":True,"async":True,"total":len(students),
                    "message":f"Sending to {len(students)} students in background…"})

@app.route("/api/email-log")
@login_required
@roles_required(*STAFF_ROLES)
def api_email_log():
    return jsonify(get_email_log(50))

# ── SSE ───────────────────────────────────────────────────────────────────────
@app.route("/stream")
@login_required
def stream():
    q = queue.Queue(maxsize=20)
    with listeners_lock:
        listeners.append(q)
    def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            with listeners_lock:
                if q in listeners:
                    listeners.remove(q)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── EXPORT ────────────────────────────────────────────────────────────────────
@app.route("/export/attendance")
@login_required
@roles_required(*STAFF_ROLES)
def export_attendance_csv():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    rows = get_full_attendance_log(dept or None, int(sem) if sem else None)
    si = io.StringIO()
    w  = csv.DictWriter(si, fieldnames=["name","usn","department","semester","section",
                                         "subject_code","subject_name","date","period_no",
                                         "status","time","marked_by"])
    w.writeheader(); w.writerows(rows)
    out = io.BytesIO(); out.write(si.getvalue().encode("utf-8")); out.seek(0)
    return send_file(out, mimetype="text/csv", as_attachment=True,
                     download_name=f"attendance_{dept or 'all'}_{date.today()}.csv")

@app.route("/export/students")
@login_required
@roles_required(*STAFF_ROLES)
def export_students_csv():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    students = get_students(dept or None, int(sem) if sem else None)
    si = io.StringIO(); w = csv.writer(si)
    w.writerow(["USN","Name","Department","Semester","Section","Email","Phone","Parent Email"])
    for s in students:
        w.writerow([s["usn"],s["name"],s["department"],s["semester"],s["section"],
                    s.get("email",""),s.get("phone",""),s.get("parent_email","")])
    out = io.BytesIO(); out.write(si.getvalue().encode("utf-8")); out.seek(0)
    return send_file(out, mimetype="text/csv", as_attachment=True,
                     download_name=f"students_{date.today()}.csv")

# ── FACE RECOGNITION ──────────────────────────────────────────────────────────

@app.route("/students/<int:sid>/enroll-face", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def enroll_face(sid):
    """Enroll (or re-enroll) a student's face from a base64 webcam snapshot."""
    s = get_student_by_id(sid)
    if not s:
        return jsonify({"success": False, "error": "Student not found"})
    data    = request.get_json() or {}
    b64_img = data.get("image", "")
    if not b64_img:
        return jsonify({"success": False, "error": "No image provided"})

    encoding, err = encode_face_from_b64(b64_img)
    if encoding is None:
        return jsonify({"success": False, "error": err})

    save_face_encoding(sid, _encoding_to_json(encoding),
                       enrolled_by=session.get("username", "admin"))
    return jsonify({"success": True,
                    "message": f"Face enrolled successfully for {s['name']}"})


@app.route("/students/<int:sid>/delete-face", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def delete_face_route(sid):
    delete_face_encoding(sid)
    return jsonify({"success": True})


@app.route("/students/<int:sid>/face-status")
@login_required
@roles_required(*STAFF_ROLES)
def face_status_route(sid):
    enc = get_face_encoding(sid)
    return jsonify({"enrolled": enc is not None,
                    "enrolled_at": enc["enrolled_at"] if enc else None})


@app.route("/attendance/face-scan")
@login_required
@roles_required(*STAFF_ROLES)
def face_scan_page():
    depts    = get_departments()
    subjects = subject_options_for_current_user()
    return render_template("face_scan.html", depts=depts, subjects=subjects,
                           username=session.get("username"), role=session.get("role"))


@app.route("/attendance/scan")
@login_required
@roles_required(*STAFF_ROLES)
def scan_attendance_page():
    """Combined QR + Face Recognition attendance scanner — glitch-free."""
    depts    = get_departments()
    subjects = subject_options_for_current_user()
    return render_template("scan_attendance.html", depts=depts, subjects=subjects,
                           username=session.get("username"), role=session.get("role"))


@app.route("/api/face-recognize", methods=["POST"])
@login_required
@roles_required(*STAFF_ROLES)
def api_face_recognize():
    """
    Receive a base64 frame, match against DB, optionally mark attendance.
    Body: { image, subject_id, period_no, mark_attendance }
    """
    ok, err = fr_check_available()
    if not ok:
        return jsonify({"success": False, "error": err})

    data        = request.get_json() or {}
    b64_img     = data.get("image", "")
    subject_id  = data.get("subject_id") or None
    period_no   = int(data.get("period_no", 1))
    do_mark     = data.get("mark_attendance", True)
    if do_mark and not can_use_subject(subject_id):
        return jsonify({"success": False, "error": "Teacher is not assigned to this subject"}), 403

    candidates  = get_all_face_encodings()
    if not candidates:
        return jsonify({"success": False,
                        "error": "No faces enrolled yet. Enrol students first."})

    sid, err = match_face_from_b64(b64_img, candidates)
    if sid is None:
        return jsonify({"success": False, "error": err or "Face not recognised."})

    student = get_student_by_id(sid)
    if not student:
        return jsonify({"success": False, "error": "Matched student not found in DB."})

    att_result = None
    if do_mark:
        mark_attendance(sid, "Present",
                        marked_by=f"face:{session.get('username','system')}",
                        subject_id=int(subject_id) if subject_id else None,
                        period_no=period_no)
        push_event({"type": "attendance_marked", "student_id": sid,
                    "name": student["name"], "dept": student["department"],
                    "sem": student["semester"], "status": "Present",
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "method": "face"})
        att_result = "marked"

    return jsonify({
        "success":    True,
        "student_id": sid,
        "name":       student["name"],
        "usn":        student["usn"],
        "department": student["department"],
        "semester":   student["semester"],
        "section":    student["section"],
        "attendance": att_result,
    })


@app.route("/api/face-enrollment-status")
@login_required
@roles_required(*STAFF_ROLES)
def api_face_enrollment_status():
    dept = request.args.get("dept", "")
    sem  = request.args.get("sem", "")
    sec  = request.args.get("sec", "")
    rows = get_face_enrollment_status(dept or None, int(sem) if sem else None,
                                      sec or None)
    ok, _ = fr_check_available()
    return jsonify({"students": rows, "fr_available": ok})


# ── QR SCAN ───────────────────────────────────────────────────────────────────
@app.route("/attendance/scan-qr-page")
@login_required
@roles_required(*STAFF_ROLES)
def qr_scan_page():
    return render_template("qr_scan.html",
                           username=session.get("username"), role=session.get("role"))

@app.route("/api/student/<path:sid>")
@login_required
def api_student(sid):
    # Try integer ID first, then fall back to USN lookup
    try:
        s = get_student_by_id(int(sid))
    except (ValueError, TypeError):
        s = None
    if not s:
        # Try USN lookup
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE LOWER(usn)=LOWER(?)", (sid,))
        row = c.fetchone()
        conn.close()
        s = dict(row) if row else None
    return jsonify(s) if s else (jsonify({"error":"Not found"}), 404)

@app.route("/print/attendance")
@login_required
@roles_required(*STAFF_ROLES)
def print_attendance():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    sec  = request.args.get("sec","")
    today_data = get_today_attendance(dept or None, int(sem) if sem else None, sec or None)
    today = date.today().strftime("%A, %d %B %Y")
    return render_template("print_attendance.html", today_data=today_data, today=today,
                           dept=dept, sem=sem, sec=sec)

@app.route("/api/subjects")
@login_required
def api_subjects():
    dept = request.args.get("dept","")
    sem  = request.args.get("sem","")
    subs = subject_options_for_current_user(dept or None, int(sem) if sem else None)
    return jsonify(subs)

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

if __name__ == "__main__":
    connect_db()
    app.run(debug=True, threaded=True, port=5000)
