# Ростелеком ЛМС

Корпоративная платформа обучения в фирменном стиле «Ростелекома». Стек: **Python FastAPI + SQLite** (бэкенд) и **HTML/CSS/JS** (фронтенд, без сборщиков).

## Возможности

- Каталог курсов с прогрессом прохождения
- Уроки с текстовыми материалами и тестами (квизами) с автопроверкой
- Регистрация и вход по токенам (Bearer), роли: `student`, `admin`
- Админ-панель: статистика, создание/удаление курсов
- Личная страница результатов
- Изначально наполнено курсами по материалам «Ростелекома» (цифровые сервисы, ИИ, развитие и забота)

## Структура

```
rolstelecom-lms/
├─ app/
│  ├─ main.py        # FastAPI-приложение и API
│  ├─ db.py          # схема и подключение к SQLite
│  ├─ seed.py        # начальные данные (курсы/уроки/тесты)
│  └─ static/        # фронтенд (index.html, css, js)
├─ requirements.txt
├─ run.py            # точка запуска
├─ Dockerfile
└─ data/lms.db       # создаётся при первом запуске
```

## Запуск локально (Windows)

Требуется Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Открыть http://127.0.0.1:8000

## Запуск через Docker

```bash
docker build -t rostelecom-lms .
docker run -p 8000:8000 -v lms-data:/srv/app/data rostelecom-lms
```

Открыть http://127.0.0.1:8000

## Учётные записи

При первом запуске БД пустая — зарегистрируйте пользователя через интерфейс (кнопка «Войти» → регистрация).
Чтобы сделать пользователя администратором, выполните:
`UPDATE users SET role='admin' WHERE username='ваш_логин';` — или через любой SQLite-клиент.

## API

| Метод | Путь | Назначение |
|-------|------|-----------|
| POST | `/api/register` | регистрация |
| POST | `/api/login` | вход, выдача токена |
| POST | `/api/logout` | выход |
| GET  | `/api/me` | текущий пользователь |
| GET  | `/api/courses` | список курсов с прогрессом |
| GET  | `/api/courses/{id}/lessons` | уроки курса |
| GET  | `/api/lessons/{id}` | урок с тестами (без ответов) |
| POST | `/api/lessons/{id}/complete` | отметить урок пройденным |
| POST | `/api/lessons/{id}/submit` | сдача теста |
| GET  | `/api/admin/stats` | статистика (admin) |
| POST/DELETE | `/api/courses` … | управление курсами (admin) |

Все защищённые эндпоинты требуют заголовок `Authorization: Bearer <token>`.