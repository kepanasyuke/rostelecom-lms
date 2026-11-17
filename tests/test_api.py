"""Офлайн-интеграционные тесты API: поднимают uvicorn на отдельной тестовой БД
и обращаются к нему через stdlib urllib (httpx/PyPI недоступны в этом окружении).

Запуск:  python -m unittest tests.test_api -v
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# тестовая БД в temp-папке (должна установиться ДО импорта app)
_TMP = Path(tempfile.mkdtemp(prefix="lms_test_"))
os.environ["LMS_DATA_DIR"] = str(_TMP)
os.environ["LMS_DB_PATH"] = str(_TMP / "lms.db")
os.environ["LMS_AUTH_RATE_LIMIT"] = "500"
os.environ["LMS_API_RATE_LIMIT"] = "1000"

import uvicorn  # noqa: E402
from app.main import RateLimiter, app  # noqa: E402

_PORT = 8123
_BASE = f"http://127.0.0.1:{_PORT}"

_uid = {"n": 0}


def _next_username():
    _uid["n"] += 1
    return f"student_{_uid['n']}"


def _request(method, path, data=None, token=None):
    url = _BASE + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


class LmsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = uvicorn.Config(app, host="127.0.0.1", port=_PORT, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(50):
            if cls.server.started:
                break
            time.sleep(0.1)
        if not cls.server.started:
            raise RuntimeError("uvicorn не стартовал")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.username = _next_username()
        st, body = _request("POST", "/api/register",
                            {"username": self.username, "password": "pass123", "full_name": "Тест"})
        self.assertEqual(st, 201, body)
        self.token = body["token"]

    def test_register_login_me(self):
        st, body = _request("GET", "/api/me", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(body["username"], self.username)

        st, body = _request("POST", "/api/login",
                            {"username": self.username, "password": "pass123"})
        self.assertEqual(st, 200)
        self.assertIn("token", body)

    def test_wrong_password(self):
        st, body = _request("POST", "/api/login",
                            {"username": self.username, "password": "wrong"})
        self.assertEqual(st, 401)

    def test_duplicate_register(self):
        st, body = _request("POST", "/api/register",
                            {"username": self.username, "password": "pass123"})
        self.assertEqual(st, 409)

    def test_courses_listed(self):
        st, body = _request("GET", "/api/courses", token=self.token)
        self.assertEqual(st, 200)
        self.assertIsInstance(body, list)

    def test_auth_required(self):
        st, body = _request("GET", "/api/courses")
        self.assertEqual(st, 401)

    def test_student_cannot_create_course(self):
        st, body = _request("POST", "/api/courses",
                            {"title": "Должно отказать"}, token=self.token)
        self.assertEqual(st, 403)

    def test_me_stats(self):
        st, body = _request("GET", "/api/me/stats", token=self.token)
        self.assertEqual(st, 200)
        self.assertIn("lessons_total", body)

    def test_me_attempts_empty(self):
        st, body = _request("GET", "/api/me/attempts", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(body, [])

    def test_me_certificates_empty(self):
        st, body = _request("GET", "/api/me/certificates", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(body, [])

    def test_validation_error(self):
        st, body = _request("POST", "/api/register", {"username": "x", "password": "y"})
        self.assertEqual(st, 422)
        self.assertIn("detail", body)


class RateLimiterUnitTest(unittest.TestCase):
    def test_limit_blocks(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.check("k")  # не должно упасть
        with self.assertRaises(Exception):
            rl.check("k")  # 4-й вызов за окно -> HTTPException(429)


if __name__ == "__main__":
    unittest.main()