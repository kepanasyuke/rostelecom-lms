import hashlib
import hmac
import json
import secrets

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pathlib import Path

from .db import get_conn, init_db
from .seed import seed

init_db()
seed()

app = FastAPI(title="Ростелеком ЛМС", version="1.0.0")

STATIC_DIR = str(Path(__file__).resolve().parent / "static")


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
        "SELECT id, username, full_name, role FROM users WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


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
def register(data: RegisterIn):
    conn = get_conn()
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (data.username,)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(status_code=409, detail="Пользователь уже существует")
    token = new_token()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, token) VALUES (?,?,?,?,?)",
        (data.username, hash_password(data.password), data.full_name, "student", token),
    )
    conn.commit()
    user_id = cur.lastrowid
    user = dict(conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone())
    conn.close()
    return {"token": token, "user": user}


@app.post("/api/login")
def login(data: LoginIn):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (data.username,)).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = new_token()
    conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
    conn.commit()
    user = dict(conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    return {"token": token, "user": user}


@app.post("/api/logout")
def logout(user: dict = Depends(require_auth)):
    conn = get_conn()
    conn.execute("UPDATE users SET token = NULL WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(require_auth)):
    return user


# ---------- courses ----------
@app.get("/api/courses")
def list_courses(user: dict = Depends(require_auth)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, description, category, icon, order_index FROM courses ORDER BY order_index, id"
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
        courses.append({**dict(r), "lessons": lesson_count, "completed": done})
    conn.close()
    return courses


@app.post("/api/courses", status_code=status.HTTP_201_CREATED)
def create_course(data: CourseIn, _: dict = Depends(require_admin)):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO courses (title, description, category, icon, order_index) VALUES (?,?,?,?,?)",
        (data.title, data.description, data.category, data.icon, data.order_index),
    )
    conn.commit()
    row = conn.execute("SELECT id, title, description, category, icon, order_index FROM courses WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, _: dict = Depends(require_admin)):
    conn = get_conn()
    conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    conn.commit()
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
def create_lesson(course_id: int, data: LessonIn, _: dict = Depends(require_admin)):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO lessons (course_id, title, content) VALUES (?,?,?)",
        (course_id, data.title, data.content),
    )
    conn.commit()
    conn.close()
    return {"id": cur.lastrowid, "course_id": course_id, "title": data.title}


@app.post("/api/lessons/{lesson_id}/quizzes", status_code=status.HTTP_201_CREATED)
def create_quiz(lesson_id: int, data: QuizIn, _: dict = Depends(require_admin)):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO quizzes (lesson_id, question, options, correct_index) VALUES (?,?,?,?)",
        (lesson_id, data.question, json.dumps(data.options, ensure_ascii=False), data.correct_index),
    )
    conn.commit()
    conn.close()
    return {"id": cur.lastrowid, "lesson_id": lesson_id}


# ---------- progress ----------
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
    conn.commit()
    conn.close()
    return {"score": score, "total": total, "percent": round(score / total * 100) if total else 0}


# ---------- admin overview ----------
@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(require_admin)):
    conn = get_conn()
    users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    students = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='student'").fetchone()["c"]
    courses = conn.execute("SELECT COUNT(*) AS c FROM courses").fetchone()["c"]
    lessons = conn.execute("SELECT COUNT(*) AS c FROM lessons").fetchone()["c"]
    completed = conn.execute("SELECT COUNT(*) AS c FROM progress").fetchone()["c"]
    conn.close()
    return {"users": users, "students": students, "courses": courses, "lessons": lessons, "completed": completed}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HTTPException)
async def http_exc_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})