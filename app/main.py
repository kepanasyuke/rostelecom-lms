import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from pathlib import Path

from .db import get_conn, init_db
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lms")

init_db()
seed()

app = FastAPI(title="Ростелеком ЛМС", version="1.0.0")

STATIC_DIR = str(Path(__file__).resolve().parent / "static")


token_ttl = timedelta(days=7)


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")
        q.append(now)


auth_limiter = RateLimiter(
    max_requests=int(os.environ.get("LMS_AUTH_RATE_LIMIT", "10")),
    window_seconds=int(os.environ.get("LMS_AUTH_RATE_WINDOW", "60")),
)
general_limiter = RateLimiter(
    max_requests=int(os.environ.get("LMS_API_RATE_LIMIT", "120")),
    window_seconds=60,
)


# ---------- auth helpers ----------
def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$", 1)[1], digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def parse_token(token: str | None) -> dict | None:
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT id, username, full_name, role, token_expires_at FROM users WHERE token = ?", (token,)
    ).fetchone()
    if row and row["token_expires_at"]:
        try:
            exp = datetime.fromisoformat(row["token_expires_at"])
        except ValueError:
            exp = None
        if exp and exp < datetime.now():
            conn.execute("UPDATE users SET token = NULL, token_expires_at = NULL WHERE id = ?", (row["id"],))
            conn.commit()
            row = None
    conn.close()
    return dict(row) if row else None


def _set_token(conn, user_id: int) -> str:
    token = new_token()
    exp = (datetime.now() + token_ttl).isoformat()
    conn.execute("UPDATE users SET token = ?, token_expires_at = ? WHERE id = ?", (token, exp, user_id))
    conn.commit()
    return token


def _log_audit(conn, user_id: int | None, action: str, meta: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (user_id, action, meta) VALUES (?,?,?)",
        (user_id, action, json.dumps(meta or {}, ensure_ascii=False)),
    )
    conn.commit()


security = HTTPBearer(auto_error=False)


def require_auth(cred: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    token = cred.credentials if cred else None
    user = parse_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return user


def require_admin(user: dict = Depends(require_auth)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужны права администратора")
    return user


def require_teacher_or_admin(user: dict = Depends(require_auth)) -> dict:
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужны права автора контента")
    return user


# ---------- schemas ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    full_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class CourseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str = ""
    icon: str = ""
    duration_hours: float = 0
    pass_score: int = Field(default=70, ge=0, le=100)
    published: bool = True
    order_index: int = 0


class LessonIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""


class QuizIn(BaseModel):
    question: str
    options: list[str]
    correct_index: int


class SubmitQuiz(BaseModel):
    answers: dict[str, int]  # lesson_id -> chosen index


# ---------- modules ----------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR + "/index.html")


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, request: Request):
    auth_limiter.check("register:" + request.client.host if request.client else "register:unknown")
    conn = get_conn()
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (data.username,)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(status_code=409, detail="Пользователь уже существует")
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
        (data.username, hash_password(data.password), data.full_name, "student"),
    )
    user_id = cur.lastrowid
    token = _set_token(conn, user_id)
    user = dict(conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone())
    _log_audit(conn, user_id, "register")
    conn.close()
    return {"token": token, "user": user}


@app.post("/api/login")
def login(data: LoginIn, request: Request):
    auth_limiter.check("login:" + (request.client.host if request.client else "login:unknown"))
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (data.username,)).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = _set_token(conn, row["id"])
    user = dict(conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (row["id"],)).fetchone())
    _log_audit(conn, row["id"], "login")
    conn.close()
    return {"token": token, "user": user}


@app.post("/api/logout")
def logout(user: dict = Depends(require_auth)):
    conn = get_conn()
    conn.execute("UPDATE users SET token = NULL, token_expires_at = NULL WHERE id = ?", (user["id"],))
    _log_audit(conn, user["id"], "logout")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(require_auth)):
    return user


# ---------- courses ----------
def _course_dict(row) -> dict:
    d = dict(row)
    if "published" in d:
        d["published"] = bool(d["published"])
    return d


@app.get("/api/courses")
def list_courses(user: dict = Depends(require_auth)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, description, category, icon, duration_hours, pass_score, published, order_index "
        "FROM courses ORDER BY order_index, id"
    ).fetchall()
    courses = []
    for r in rows:
        lesson_count = conn.execute(
            "SELECT COUNT(*) AS c FROM lessons WHERE course_id = ?", (r["id"],)
        ).fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(DISTINCT p.lesson_id) AS c FROM progress p JOIN lessons l ON l.id = p.lesson_id "
            "WHERE p.user_id = ? AND l.course_id = ?",
            (user["id"], r["id"]),
        ).fetchone()["c"]
        courses.append({**_course_dict(r), "lessons": lesson_count, "completed": done})
    conn.close()
    return courses


@app.post("/api/courses", status_code=status.HTTP_201_CREATED)
def create_course(data: CourseIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO courses (title, description, category, icon, duration_hours, pass_score, owner_id, published, order_index) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            data.title, data.description, data.category, data.icon,
            data.duration_hours, data.pass_score,
            user["id"] if user["role"] == "teacher" else None,
            int(data.published), data.order_index,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM courses WHERE id = ?", (cur.lastrowid,)).fetchone()
    _log_audit(conn, user["id"], "course.create", {"course_id": cur.lastrowid})
    conn.close()
    return _course_dict(row)


@app.put("/api/courses/{course_id}")
def update_course(course_id: int, data: CourseIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Курс не найден")
    conn.execute(
        "UPDATE courses SET title=?, description=?, category=?, icon=?, duration_hours=?, pass_score=?, "
        "published=?, order_index=? WHERE id=?",
        (
            data.title, data.description, data.category, data.icon,
            data.duration_hours, data.pass_score, int(data.published), data.order_index, course_id,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    _log_audit(conn, user["id"], "course.update", {"course_id": course_id})
    conn.close()
    return _course_dict(updated)


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    row = conn.execute("SELECT id FROM courses WHERE id = ?", (course_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Курс не найден")
    conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    conn.commit()
    _log_audit(conn, user["id"], "course.delete", {"course_id": course_id})
    conn.close()
    return {"ok": True}


# ---------- lessons & quizzes ----------
@app.get("/api/courses/{course_id}/lessons")
def list_lessons(course_id: int, user: dict = Depends(require_auth)):
    conn = get_conn()
    course = conn.execute("SELECT id, title FROM courses WHERE id = ?", (course_id,)).fetchone()
    if not course:
        conn.close()
        raise HTTPException(status_code=404, detail="Курс не найден")
    rows = conn.execute(
        "SELECT id, course_id, title, order_index FROM lessons WHERE course_id = ? ORDER BY order_index, id",
        (course_id,),
    ).fetchall()
    result = []
    for l in rows:
        p = conn.execute(
            "SELECT quiz_score, quiz_max FROM progress WHERE user_id = ? AND lesson_id = ?",
            (user["id"], l["id"]),
        ).fetchone()
        quiz_count = conn.execute("SELECT COUNT(*) AS c FROM quizzes WHERE lesson_id = ?", (l["id"],)).fetchone()["c"]
        result.append({
            **dict(l),
            "quiz_count": quiz_count,
            "completed": p is not None,
            "score": p["quiz_score"] if p else None,
            "score_max": p["quiz_max"] if p else None,
        })
    conn.close()
    return {"course": dict(course), "lessons": result}


@app.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: int, user: dict = Depends(require_auth)):
    conn = get_conn()
    l = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    quizzes = conn.execute("SELECT id, question, options FROM quizzes WHERE lesson_id = ?", (lesson_id,)).fetchall()
    p = conn.execute(
        "SELECT quiz_score, quiz_max FROM progress WHERE user_id = ? AND lesson_id = ?",
        (user["id"], lesson_id),
    ).fetchone()
    conn.close()
    return {
        "lesson": dict(l),
        "quizzes": [{"id": q["id"], "question": q["question"], "options": json.loads(q["options"])} for q in quizzes],
        "progress": dict(p) if p else None,
    }


@app.post("/api/courses/{course_id}/lessons", status_code=status.HTTP_201_CREATED)
def create_lesson(course_id: int, data: LessonIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    course = conn.execute("SELECT id FROM courses WHERE id = ?", (course_id,)).fetchone()
    if not course:
        conn.close()
        raise HTTPException(status_code=404, detail="Курс не найден")
    order = conn.execute("SELECT COUNT(*) AS c FROM lessons WHERE course_id = ?", (course_id,)).fetchone()["c"]
    cur = conn.execute(
        "INSERT INTO lessons (course_id, title, content, order_index) VALUES (?,?,?,?)",
        (course_id, data.title, data.content, order),
    )
    conn.commit()
    _log_audit(conn, user["id"], "lesson.create", {"course_id": course_id, "lesson_id": cur.lastrowid})
    conn.close()
    return {"id": cur.lastrowid, "course_id": course_id, "title": data.title}


@app.put("/api/lessons/{lesson_id}")
def update_lesson(lesson_id: int, data: LessonIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    l = conn.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    conn.execute("UPDATE lessons SET title=?, content=? WHERE id=?", (data.title, data.content, lesson_id))
    conn.commit()
    _log_audit(conn, user["id"], "lesson.update", {"lesson_id": lesson_id})
    conn.close()
    return {"id": lesson_id, "title": data.title}


@app.delete("/api/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    l = conn.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    conn.commit()
    _log_audit(conn, user["id"], "lesson.delete", {"lesson_id": lesson_id})
    conn.close()
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/quizzes", status_code=status.HTTP_201_CREATED)
def create_quiz(lesson_id: int, data: QuizIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    l = conn.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    cur = conn.execute(
        "INSERT INTO quizzes (lesson_id, question, options, correct_index) VALUES (?,?,?,?)",
        (lesson_id, data.question, json.dumps(data.options, ensure_ascii=False), data.correct_index),
    )
    conn.commit()
    _log_audit(conn, user["id"], "quiz.create", {"lesson_id": lesson_id})
    conn.close()
    return {"id": cur.lastrowid, "lesson_id": lesson_id}


@app.put("/api/quizzes/{quiz_id}")
def update_quiz(quiz_id: int, data: QuizIn, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    q = conn.execute("SELECT id FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if not q:
        conn.close()
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    conn.execute(
        "UPDATE quizzes SET question=?, options=?, correct_index=? WHERE id=?",
        (data.question, json.dumps(data.options, ensure_ascii=False), data.correct_index, quiz_id),
    )
    conn.commit()
    _log_audit(conn, user["id"], "quiz.update", {"quiz_id": quiz_id})
    conn.close()
    return {"id": quiz_id}


@app.delete("/api/quizzes/{quiz_id}")
def delete_quiz(quiz_id: int, user: dict = Depends(require_teacher_or_admin)):
    conn = get_conn()
    q = conn.execute("SELECT id FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if not q:
        conn.close()
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    conn.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    conn.commit()
    _log_audit(conn, user["id"], "quiz.delete", {"quiz_id": quiz_id})
    conn.close()
    return {"ok": True}


# ---------- progress ----------
def _maybe_issue_certificate(conn, user_id: int, course_id: int) -> dict | None:
    # курс засчитан, если все уроки пройдены и средний балл >= pass_score курса
    course = conn.execute("SELECT pass_score FROM courses WHERE id = ?", (course_id,)).fetchone()
    if not course:
        return None
    lessons = conn.execute("SELECT id FROM lessons WHERE course_id = ?", (course_id,)).fetchall()
    if not lessons:
        return None
    done = conn.execute(
        "SELECT COUNT(*) AS c FROM progress p WHERE p.user_id = ? AND p.lesson_id IN "
        "(SELECT id FROM lessons WHERE course_id = ?)",
        (user_id, course_id),
    ).fetchone()["c"]
    if done != len(lessons):
        return None
    avg_percent = conn.execute(
        "SELECT AVG(100.0 * quiz_score / quiz_max) AS a FROM progress p "
        "JOIN lessons l ON l.id = p.lesson_id WHERE p.user_id = ? AND l.course_id = ? AND p.quiz_max > 0",
        (user_id, course_id),
    ).fetchone()["a"]
    if avg_percent is None or avg_percent < course["pass_score"]:
        return None
    exists = conn.execute(
        "SELECT code FROM certificates WHERE user_id = ? AND course_id = ?", (user_id, course_id)
    ).fetchone()
    if exists:
        return dict(exists)
    code = "RT-" + secrets.token_hex(4).upper()
    conn.execute(
        "INSERT OR IGNORE INTO certificates (user_id, course_id, code) VALUES (?,?,?)",
        (user_id, course_id, code),
    )
    _log_audit(conn, user_id, "certificate.issue", {"course_id": course_id})
    return {"code": code}


@app.get("/api/me/stats")
def me_stats(user: dict = Depends(require_auth)):
    conn = get_conn()
    courses_total = conn.execute("SELECT COUNT(*) AS c FROM courses WHERE published = 1").fetchone()["c"]
    lessons_total = conn.execute("SELECT COUNT(*) AS c FROM lessons").fetchone()["c"]
    completed_lessons = conn.execute(
        "SELECT COUNT(*) AS c FROM progress WHERE user_id = ?", (user["id"],)
    ).fetchone()["c"]
    avg_score = conn.execute(
        "SELECT AVG(100.0 * quiz_score / quiz_max) AS a FROM progress WHERE user_id = ? AND quiz_max > 0",
        (user["id"],),
    ).fetchone()["a"]
    certs = conn.execute(
        "SELECT COUNT(*) AS c FROM certificates WHERE user_id = ?", (user["id"],)
    ).fetchone()["c"]
    conn.close()
    return {
        "courses_total": courses_total,
        "lessons_total": lessons_total,
        "completed_lessons": completed_lessons,
        "average_score": round(avg_score, 1) if avg_score is not None else None,
        "certificates": certs,
    }


@app.get("/api/me/certificates")
def me_certificates(user: dict = Depends(require_auth)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT c.id, c.code, c.issued_at, co.title AS course_title "
        "FROM certificates c JOIN courses co ON co.id = c.course_id "
        "WHERE c.user_id = ? ORDER BY c.issued_at DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/me/attempts")
def me_attempts(user: dict = Depends(require_auth)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.id, a.lesson_id, a.score, a.total, a.attempted_at, l.title AS lesson_title "
        "FROM attempts a JOIN lessons l ON l.id = a.lesson_id "
        "WHERE a.user_id = ? ORDER BY a.attempted_at DESC LIMIT 100",
        (user["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
@app.post("/api/lessons/{lesson_id}/complete")
def mark_complete(lesson_id: int, user: dict = Depends(require_auth)):
    conn = get_conn()
    l = conn.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    conn.execute(
        "INSERT OR IGNORE INTO progress (user_id, lesson_id, quiz_score, quiz_max) VALUES (?,?,?,?)",
        (user["id"], lesson_id, None, None),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/submit")
def submit_quiz(lesson_id: int, data: SubmitQuiz, user: dict = Depends(require_auth)):
    conn = get_conn()
    l = conn.execute("SELECT id FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(status_code=404, detail="Урок не найден")
    if data.answers is None:
        raise HTTPException(status_code=400, detail="Нет ответов")
    quizzes = conn.execute("SELECT id, correct_index FROM quizzes WHERE lesson_id = ?", (lesson_id,)).fetchall()
    score = 0
    for q in quizzes:
        chosen = data.answers.get(str(q["id"]))
        if chosen == q["correct_index"]:
            score += 1
    total = len(quizzes)
    conn.execute(
        "INSERT OR REPLACE INTO progress (user_id, lesson_id, quiz_score, quiz_max, completed_at) "
        "VALUES (?,?,?,?, datetime('now'))",
        (user["id"], lesson_id, score, total),
    )
    conn.execute(
        "INSERT INTO attempts (user_id, lesson_id, score, total, answers) VALUES (?,?,?,?,?)",
        (user["id"], lesson_id, score, total, json.dumps(data.answers, ensure_ascii=False)),
    )
    cert = _maybe_issue_certificate(conn, user["id"], l["course_id"])
    conn.commit()
    conn.close()
    result = {"score": score, "total": total, "percent": round(score / total * 100) if total else 0}
    if cert:
        result["certificate"] = cert
    return result


# ---------- admin: users ----------
class RoleIn(BaseModel):
    role: str = Field(pattern="^(student|teacher|admin)$")


@app.get("/api/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.full_name, u.role, u.created_at, "
        "(SELECT COUNT(*) FROM progress p WHERE p.user_id = u.id) AS completed_lessons "
        "FROM users u ORDER BY u.id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.put("/api/admin/users/{user_id}/role")
def admin_set_role(user_id: int, data: RoleIn, admin: dict = Depends(require_admin)):
    conn = get_conn()
    row = conn.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (data.role, user_id))
    conn.commit()
    _log_audit(conn, admin["id"], "user.role", {"target": user_id, "role": data.role})
    conn.close()
    return {"id": user_id, "role": data.role}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    conn = get_conn()
    if admin["id"] == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя удалить свою учётную запись")
    row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    _log_audit(conn, admin["id"], "user.delete", {"target": user_id})
    conn.close()
    return {"ok": True}


# ---------- admin overview ----------
@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(require_admin)):
    conn = get_conn()
    users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    students = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='student'").fetchone()["c"]
    courses = conn.execute("SELECT COUNT(*) AS c FROM courses").fetchone()["c"]
    lessons = conn.execute("SELECT COUNT(*) AS c FROM lessons").fetchone()["c"]
    completed = conn.execute("SELECT COUNT(*) AS c FROM progress").fetchone()["c"]
    attempts = conn.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"]
    certificates = conn.execute("SELECT COUNT(*) AS c FROM certificates").fetchone()["c"]
    conn.close()
    return {
        "users": users, "students": students, "courses": courses, "lessons": lessons,
        "completed": completed, "attempts": attempts, "certificates": certificates,
    }


@app.get("/api/admin/reports/progress")
def admin_progress_report(_: dict = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.full_name, "
        "(SELECT COUNT(*) FROM progress p WHERE p.user_id = u.id) AS completed_lessons, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.user_id = u.id) AS attempts, "
        "(SELECT COUNT(*) FROM certificates c WHERE c.user_id = u.id) AS certificates "
        "FROM users u WHERE u.role = 'student' ORDER BY completed_lessons DESC, u.id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/admin/reports/courses")
def admin_course_report(_: dict = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT co.id, co.title, co.published, "
        "(SELECT COUNT(*) FROM lessons l WHERE l.course_id = co.id) AS lessons, "
        "(SELECT COUNT(DISTINCT p.user_id) FROM progress p JOIN lessons l ON l.id = p.lesson_id "
        " WHERE l.course_id = co.id) AS learners, "
        "(SELECT COUNT(*) FROM certificates c WHERE c.course_id = co.id) AS certificates "
        "FROM courses co ORDER BY co.id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/admin/audit")
def admin_audit(_: dict = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.id, a.user_id, u.username, a.action, a.meta, a.created_at "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        "ORDER BY a.id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api"):
        host = request.client.host if request.client else "unknown"
        try:
            general_limiter.check("api:" + host)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("%s %s -> %s (%sms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HTTPException)
async def http_exc_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request, exc):
    errors = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", []))
        errors.append({"field": loc, "message": e.get("msg", "Некорректное значение")})
    logger.warning("validation error %s %s: %s", request.method, request.url.path, errors)
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
async def unhandled_exc_handler(request, exc):
    logger.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})