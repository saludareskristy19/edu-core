import os
import sqlite3
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
DB_PATH = os.environ.get("DB_PATH", "sis.db")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

ROLES = ("student", "teacher", "admin")
SUBJECTS = ["Mathematics", "English", "Science", "Filipino",
            "Computer Programming", "Social Studies", "MAPEH"]


# =====================================================================
#  DATABASE
# =====================================================================
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quizzes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            subject TEXT NOT NULL, title TEXT NOT NULL, due TEXT,
            done INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS progress(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            subject TEXT NOT NULL, completed INTEGER NOT NULL, total INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS grades(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            subject TEXT NOT NULL, grade REAL NOT NULL);
        """)
        try:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
        except sqlite3.OperationalError:
            pass  # column already exists
        # the admin account is rebuilt on every start from the environment values
        c.execute(
            "INSERT OR REPLACE INTO users(username,name,password,role) VALUES (?,?,?,'admin')",
            (ADMIN_USERNAME, "Administrator", generate_password_hash(ADMIN_PASSWORD)))


init_db()


# =====================================================================
#  HELPERS
# =====================================================================
def me():
    return session["user"]["username"]


def role():
    return session.get("user", {}).get("role")


def roles_required(*allowed):
    """Only let the listed roles open a page. Everyone else goes home."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if role() not in allowed:
                return redirect(url_for("home"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


student_required = roles_required("student")
staff_required = roles_required("admin", "teacher")
admin_required = roles_required("admin")


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_student(username):
    return db().execute(
        "SELECT username, name FROM users WHERE username=? AND role='student'",
        (username,)).fetchone()


def student_rows():
    return db().execute("""
        SELECT u.username, u.name,
          (SELECT COUNT(*) FROM quizzes q WHERE q.username=u.username AND q.done=0) AS pending,
          (SELECT COUNT(*) FROM quizzes q WHERE q.username=u.username AND q.done=1) AS finished,
          (SELECT ROUND(AVG(grade),2) FROM grades g WHERE g.username=u.username) AS average
        FROM users u WHERE u.role='student' ORDER BY u.name""").fetchall()


def average_of(grades):
    return round(sum(r["grade"] for r in grades) / len(grades), 2) if grades else 0


def delete_user_data(username):
    for table in ("quizzes", "progress", "grades"):
        db().execute(f"DELETE FROM {table} WHERE username=?", (username,))
    db().execute("DELETE FROM users WHERE username=?", (username,))
    db().commit()


# =====================================================================
#  HOME  (sends each role to its own page)
# =====================================================================
@app.route("/")
def home():
    user = session.get("user")
    if not user:
        return render_template("index.html", user=None)
    if user["role"] in ("admin", "teacher"):
        return redirect(url_for("staff"))

    tab = "done" if request.args.get("tab") == "done" else "pending"
    quizzes = db().execute(
        "SELECT * FROM quizzes WHERE username=? AND done=? ORDER BY due",
        (me(), 1 if tab == "done" else 0)).fetchall()
    progress = db().execute(
        "SELECT * FROM progress WHERE username=? ORDER BY id", (me(),)).fetchall()
    grades = db().execute(
        "SELECT * FROM grades WHERE username=? ORDER BY id", (me(),)).fetchall()

    return render_template("index.html", user=user, subjects=SUBJECTS, tab=tab,
                           quizzes=quizzes, progress=progress, grades=grades,
                           average=average_of(grades))


# =====================================================================
#  AUTH
# =====================================================================
@app.route("/signup", methods=["POST"])
def signup():
    """Public sign up always creates a STUDENT. Staff accounts come from the admin."""
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    if not name or not username or len(password) < 4:
        flash("Fill in every field. Password needs at least 4 characters.", "error")
    elif db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash("That username is taken. Choose another one.", "error")
    else:
        db().execute(
            "INSERT INTO users(username,name,password,role) VALUES (?,?,?, 'student')",
            (username, name, generate_password_hash(password)))
        db().commit()
        flash("Account created. You can log in now.", "ok")
    return redirect(url_for("home"))


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    row = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and check_password_hash(row["password"], password):
        session["user"] = {"username": row["username"], "name": row["name"],
                           "role": row["role"]}
    else:
        flash("Wrong username or password.", "error")
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# =====================================================================
#  STUDENT: missing quizzes
# =====================================================================
@app.route("/quiz/add", methods=["POST"])
@student_required
def quiz_add():
    title = request.form.get("title", "").strip()
    if title:
        db().execute(
            "INSERT INTO quizzes(username,subject,title,due) VALUES (?,?,?,?)",
            (me(), request.form.get("subject", ""), title, request.form.get("due", "")))
        db().commit()
    return redirect(url_for("home") + "#quizzes")


@app.route("/quiz/<int:qid>/toggle", methods=["POST"])
@student_required
def quiz_toggle(qid):
    db().execute("UPDATE quizzes SET done = 1 - done WHERE id=? AND username=?",
                 (qid, me()))
    db().commit()
    return redirect(request.referrer or url_for("home"))


@app.route("/quiz/<int:qid>/delete", methods=["POST"])
@student_required
def quiz_delete(qid):
    db().execute("DELETE FROM quizzes WHERE id=? AND username=?", (qid, me()))
    db().commit()
    return redirect(request.referrer or url_for("home"))


# =====================================================================
#  STUDENT: progress tracker
# =====================================================================
@app.route("/progress/add", methods=["POST"])
@student_required
def progress_add():
    total = max(1, to_int(request.form.get("total"), 10))
    completed = min(max(0, to_int(request.form.get("completed"))), total)
    db().execute(
        "INSERT INTO progress(username,subject,completed,total) VALUES (?,?,?,?)",
        (me(), request.form.get("subject", ""), completed, total))
    db().commit()
    return redirect(url_for("home") + "#progress")


@app.route("/progress/<int:pid>/step/<int:step>", methods=["POST"])
@student_required
def progress_step(pid, step):
    step = 1 if step else -1
    db().execute(
        "UPDATE progress SET completed = MAX(0, MIN(total, completed + ?)) "
        "WHERE id=? AND username=?", (step, pid, me()))
    db().commit()
    return redirect(url_for("home") + "#progress")


@app.route("/progress/<int:pid>/delete", methods=["POST"])
@student_required
def progress_delete(pid):
    db().execute("DELETE FROM progress WHERE id=? AND username=?", (pid, me()))
    db().commit()
    return redirect(url_for("home") + "#progress")


# =====================================================================
#  STUDENT: final grades
# =====================================================================
def parse_grade(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


@app.route("/grade/add", methods=["POST"])
@student_required
def grade_add():
    grade = parse_grade(request.form.get("grade"))
    if grade is not None:
        db().execute("INSERT INTO grades(username,subject,grade) VALUES (?,?,?)",
                     (me(), request.form.get("subject", ""), grade))
        db().commit()
    return redirect(url_for("home") + "#grades")


@app.route("/grade/<int:gid>/delete", methods=["POST"])
@student_required
def grade_delete(gid):
    db().execute("DELETE FROM grades WHERE id=? AND username=?", (gid, me()))
    db().commit()
    return redirect(url_for("home") + "#grades")


# =====================================================================
#  STAFF (teacher + admin): see students, assign quizzes, set grades
# =====================================================================
@app.route("/staff")
@staff_required
def staff():
    accounts = None
    if role() == "admin":
        accounts = db().execute(
            "SELECT username, name, role FROM users WHERE role != 'student' "
            "ORDER BY role, name").fetchall()
    return render_template("index.html", user=session["user"],
                           students=student_rows(), accounts=accounts)


@app.route("/staff/student/<username>")
@staff_required
def staff_student(username):
    student = get_student(username)
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("staff"))
    quizzes = db().execute(
        "SELECT * FROM quizzes WHERE username=? ORDER BY done, due", (username,)).fetchall()
    progress = db().execute(
        "SELECT * FROM progress WHERE username=? ORDER BY id", (username,)).fetchall()
    grades = db().execute(
        "SELECT * FROM grades WHERE username=? ORDER BY id", (username,)).fetchall()
    return render_template("index.html", user=session["user"], student=student,
                           subjects=SUBJECTS, quizzes=quizzes, progress=progress,
                           grades=grades, average=average_of(grades))


@app.route("/staff/student/<username>/quiz/add", methods=["POST"])
@staff_required
def staff_quiz_add(username):
    title = request.form.get("title", "").strip()
    if get_student(username) and title:
        db().execute(
            "INSERT INTO quizzes(username,subject,title,due) VALUES (?,?,?,?)",
            (username, request.form.get("subject", ""), title, request.form.get("due", "")))
        db().commit()
        flash("Quiz assigned.", "ok")
    return redirect(url_for("staff_student", username=username))


@app.route("/staff/student/<username>/quiz/<int:qid>/delete", methods=["POST"])
@staff_required
def staff_quiz_delete(username, qid):
    db().execute("DELETE FROM quizzes WHERE id=? AND username=?", (qid, username))
    db().commit()
    return redirect(url_for("staff_student", username=username))


@app.route("/staff/student/<username>/grade/add", methods=["POST"])
@staff_required
def staff_grade_add(username):
    grade = parse_grade(request.form.get("grade"))
    if get_student(username) and grade is not None:
        db().execute("INSERT INTO grades(username,subject,grade) VALUES (?,?,?)",
                     (username, request.form.get("subject", ""), grade))
        db().commit()
        flash("Grade saved.", "ok")
    else:
        flash("Enter a grade from 0 to 100.", "error")
    return redirect(url_for("staff_student", username=username))


@app.route("/staff/student/<username>/grade/<int:gid>/delete", methods=["POST"])
@staff_required
def staff_grade_delete(username, gid):
    db().execute("DELETE FROM grades WHERE id=? AND username=?", (gid, username))
    db().commit()
    return redirect(url_for("staff_student", username=username))


# =====================================================================
#  ADMIN ONLY: create, reset password, delete accounts
# =====================================================================
@app.route("/admin/create", methods=["POST"])
@admin_required
def admin_create():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    new_role = request.form.get("role", "student")
    if new_role not in ROLES:
        new_role = "student"
    if not name or not username or len(password) < 4:
        flash("Fill in every field. Password needs at least 4 characters.", "error")
    elif db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash("That username is taken.", "error")
    else:
        db().execute(
            "INSERT INTO users(username,name,password,role) VALUES (?,?,?,?)",
            (username, name, generate_password_hash(password), new_role))
        db().commit()
        flash(f"{new_role.capitalize()} account created.", "ok")
    return redirect(url_for("staff"))


@app.route("/admin/reset/<username>", methods=["POST"])
@admin_required
def admin_reset(username):
    password = request.form.get("password", "")
    if len(password) < 4:
        flash("New password needs at least 4 characters.", "error")
    else:
        db().execute("UPDATE users SET password=? WHERE username=?",
                     (generate_password_hash(password), username))
        db().commit()
        flash("Password updated.", "ok")
    return redirect(url_for("staff"))


@app.route("/admin/delete/<username>", methods=["POST"])
@admin_required
def admin_delete(username):
    if username == me():
        flash("You cannot delete your own account.", "error")
    else:
        delete_user_data(username)
        flash("Account deleted.", "ok")
    return redirect(url_for("staff"))