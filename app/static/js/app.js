const $ = (s) => document.querySelector(s);
const api = {
  token: localStorage.getItem("rt_token") || "",
  headers() { return this.token ? { "Authorization": "Bearer " + this.token, "Content-Type": "application/json" } : { "Content-Type": "application/json" }; },
  async call(method, url, body) {
    const res = await fetch(url, { method, headers: this.headers(), body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Ошибка запроса");
    return data;
  },
  get(url) { return this.call("GET", url); },
  post(url, body) { return this.call("POST", url, body); },
  del(url) { return this.call("DELETE", url); },
};

const state = { user: null, courses: [], course: null };

const icons = { wifi: "📡", ai: "🤖", growth: "🌱", default: "📚" };

function toast(text, kind) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast show " + (kind || "");
  setTimeout(() => { el.className = "toast"; }, 2600);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function show(id) {
  ["home", "stats", "admin", "course", "lesson"].forEach((v) => { $("#view-" + v).style.display = v === id ? "block" : "none"; });
  return id;
}

function renderAuth() {
  const login = $("#btn-login");
  const userBadge = $("#user-badge");
  const logout = $("#btn-logout");
  const adminNav = document.querySelectorAll(".admin-only");
  const hasUser = !!state.user;
  login.style.display = hasUser ? "none" : "";
  logout.style.display = hasUser ? "" : "none";
  userBadge.style.display = hasUser ? "" : "none";
  userBadge.textContent = hasUser ? state.user.full_name || state.user.username : "";
  adminNav.forEach((el) => { el.style.display = hasUser && state.user.role === "admin" ? "" : "none"; });
}

async function loadSession() {
  let user = null;
  if (api.token) {
    try { user = await api.get("/api/me"); }
    catch { api.token = ""; localStorage.removeItem("rt_token"); }
  }
  state.user = user;
  renderAuth();
}

function pct(c) {
  return !c.lessons ? 0 : Math.round((c.completed || 0) / c.lessons * 100);
}

function courseCard(c) {
  const icon = c.icon && icons[c.icon] ? icons[c.icon] : icons.default;
  const p = pct(c);
  return '<div class="card" onclick="openCourse(' + c.id + ')">'
    + '<div class="card__icon">' + icon + '</div>'
    + '<div class="card__cat">' + esc(c.category) + '</div>'
    + '<h3 class="card__title">' + esc(c.title) + '</h3>'
    + '<p class="card__desc">' + esc(c.description) + '</p>'
    + '<div class="card__meta"><span>' + c.completed + '/' + c.lessons + ' уроков</span><span>' + p + '%</span></div>'
    + '<div class="progress"><div class="progress__bar" style="width:' + p + '%"></div></div>'
    + '</div>';
}

async function loadCourses() {
  let html = "";
  if (state.user) {
    state.courses = await api.get("/api/courses");
    const q = ($("#search").value || "").toLowerCase();
    const list = state.courses.filter((c) => !q || c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q));
    html = list.length ? list.map(courseCard).join("") : '<div class="empty"><strong>Ничего не найдено</strong>Попробуйте изменить запрос</div>';
  }
  $("#courses").innerHTML = html;
}

async function openCourse(id) {
  show("course");
  const data = await api.get("/api/courses/" + id + "/lessons");
  state.course = data;
  const c = data.course;
  $("#course-detail").innerHTML = '<div class="course-hero">'
    + '<div class="card__cat">' + esc(data.course.category || "") + '</div>'
    + '<h2 class="course-hero__title">' + esc(c.title) + '</h2>'
    + '<p class="course-hero__desc">' + esc(data.course.description || "") + '</p>'
    + '<div class="lesson-list">'
    + data.lessons.map((l) =>
        '<div class="lesson-row' + (l.completed ? " done" : "") + '" onclick="openLesson(' + l.id + ')">'
        + '<span class="lesson-row__idx">' + (data.lessons.indexOf(l) + 1) + '</span>'
        + '<span class="lesson-row__title">' + esc(l.title) + '</span>'
        + '<span class="lesson-row__meta">' + l.quiz_count + ' тест' + (l.quiz_count === 1 ? "" : "а") + '</span>'
        + '<span class="lesson-row__check">' + (l.completed ? "✓" : "") + '</span>'
        + '</div>')
    .join("") + '</div></div>';
}

async function openLesson(id) {
  show("lesson");
  const data = await api.get("/api/lessons/" + id);
  const l = data.lesson;
  const quizHtml = data.quizzes.map((q, qi) =>
    '<div class="quiz-item">'
    + '<div class="quiz-item__q">' + esc(q.question) + '</div>'
    + '<div class="quiz-item__opts">'
    + q.options.map((o, oi) =>
        '<label class="quiz-item__opt"><input type="radio" name="q' + q.id + '" value="' + oi + '">' + esc(o) + '</label>')
    .join("") + '</div></div>').join("");

  $("#lesson-detail").innerHTML = '<div class="lesson-content">'
    + '<h3>' + esc(l.title) + '</h3><p>' + esc(l.content).replace(/\n/g, "<br>") + '</p>'
    + (data.quizzes.length ? '<div class="quiz-block"><h4 class="quiz-title">Проверьте себя</h4>' + quizHtml
        + '<button class="btn btn--primary" style="margin-top:8px" onclick="submitQuiz(' + l.id + ')">Отправить ответы</button></div>' : "")
    + (data.progress ? '<p style="margin-top:20px;color:#1a7f37;font-weight:700">Результат: ' + data.progress.quiz_score + '/' + data.progress.quiz_max + '</p>' : "")
    + '</div>';
}

async function submitQuiz(lessonId) {
  const answers = {};
  document.querySelectorAll("#lesson-detail input[type=radio]:checked").forEach((inp) => {
    answers[inp.name.replace("q", "")] = Number(inp.value);
  });
  if (Object.keys(answers).length) {
    try {
      const r = await api.post("/api/lessons/" + lessonId + "/submit", { answers });
      toast("Правильно: " + r.score + " из " + r.total + " (" + r.percent + "%)", "success");
      await loadCourses();
    } catch (e) { toast(e.message, "error"); }
  } else {
    toast("Выберите хотя бы один ответ", "error");
  }
}

async function renderStats() {
  let html = "";
  if (state.user) {
    const courses = await api.get("/api/courses");
    const totalLessons = courses.reduce((a, c) => a + (c.lessons || 0), 0);
    const totalDone = courses.reduce((a, c) => a + (c.completed || 0), 0);
    const doneCourses = courses.filter((c) => c.lessons && c.completed === c.lessons).length;
    html =
      '<div class="stat"><div class="stat__num">' + totalDone + '/' + totalLessons + '</div><div class="stat__label">пройдено уроков</div></div>'
      + '<div class="stat"><div class="stat__num">' + doneCourses + '/' + courses.length + '</div><div class="stat__label">курсов завершено</div></div>'
      + '<div class="stat"><div class="stat__num">' + Math.round(totalLessons ? totalDone / totalLessons * 100 : 0) + '%</div><div class="stat__label">общий прогресс</div></div>';
  }
  $("#stats").innerHTML = html;
}

async function renderAdmin() {
  let html = "";
  if (state.user && state.user.role === "admin") {
    const stats = await api.get("/api/admin/stats");
    const courses = state.courses.length ? state.courses : await api.get("/api/courses");
    html =
      '<div class="stats">'
      + '<div class="stat"><div class="stat__num">' + stats.users + '</div><div class="stat__label">пользователей</div></div>'
      + '<div class="stat"><div class="stat__num">' + stats.courses + '</div><div class="stat__label">курсов</div></div>'
      + '<div class="stat"><div class="stat__num">' + stats.lessons + '</div><div class="stat__label">уроков</div></div>'
      + '<div class="stat"><div class="stat__num">' + stats.completed + '</div><div class="stat__label">завершений</div></div>'
      + '</div>'
      + '<div class="admin-grid"><div class="admin-panel"><h3>Курсы</h3>'
      + courses.map((c) => '<div class="admin-row"><span class="admin-row__title">' + esc(c.title) + '</span>'
          + '<button class="btn btn--ghost danger" onclick="deleteCourse(' + c.id + ')">Удалить</button></div>').join("")
      + '</div></div>';
  }
  $("#admin-section").innerHTML = html;
}

async function deleteCourse(id) {
  if (confirm("Удалить курс?")) {
    try { await api.del("/api/courses/" + id); toast("Курс удалён", "success"); await loadCourses(); }
    catch (e) { toast(e.message, "error"); }
  }
}

function openModal(mode) {
  const modal = $("#auth-modal");
  const isLogin = mode !== "register";
  $("#auth-title").textContent = isLogin ? "Вход в платформу" : "Регистрация";
  $("#auth-submit").textContent = isLogin ? "Войти" : "Зарегистрироваться";
  $("#name-field").style.display = isLogin ? "none" : "";
  $("#register-hint").innerHTML = isLogin ? 'Нет аккаунта? <a href="#" id="toggle-auth">Зарегистрироваться</a>' : 'Уже есть аккаунт? <a href="#" id="toggle-auth">Войти</a>';
  $("#auth-error").textContent = "";
  modal.classList.add("open");
}

function closeModal() { $("#auth-modal").classList.remove("open"); }

async function submitAuth(e) {
  e.preventDefault();
  const form = e.target;
  const body = { username: form.username.value.trim(), password: form.password.value };
  const isLogin = $("#auth-submit").textContent === "Войти";
  if (!isLogin) body.full_name = form.full_name.value.trim();
  try {
    const r = await api.post("/api/" + (isLogin ? "login" : "register"), body);
    api.token = r.token;
    localStorage.setItem("rt_token", r.token);
    state.user = r.user;
    closeModal();
    form.reset();
    renderAuth();
    toast("Добро пожаловать, " + (r.user.full_name || r.user.username) + "!", "success");
    await refreshAll();
  } catch (err) { $("#auth-error").textContent = err.message; }
}

async function logout() {
  try { await api.post("/api/logout"); } catch {}
  api.token = "";
  localStorage.removeItem("rt_token");
  state.user = null;
  renderAuth();
  $("#courses").innerHTML = '<div class="empty"><strong>Войдите в систему</strong>Чтобы видеть курсы, авторизуйтесь</div>';
  show("home");
}

async function refreshAll() {
  await loadCourses();
  if (state.user) renderStats();
}

function attach() {
  $("#btn-login").addEventListener("click", () => openModal("login"));
  $("#auth-close").addEventListener("click", closeModal);
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#btn-logout").addEventListener("click", logout);
  $("#search").addEventListener("input", loadCourses);
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = el.getAttribute("data-nav");
      show(id);
      if (id === "stats") renderStats();
      if (id === "admin") renderAdmin();
      if (id === "home") loadCourses();
    });
  });
  document.querySelectorAll("[data-scroll]").forEach((el) => { el.addEventListener("click", () => show("home")); });
  $("#auth-form").addEventListener("click", (e) => {
    if (e.target.id === "toggle-auth") { e.preventDefault(); openModal($("#auth-submit").textContent === "Войти" ? "register" : "login"); }
  });
}

window.openCourse = openCourse;
window.openLesson = openLesson;
window.submitQuiz = submitQuiz;
window.deleteCourse = deleteCourse;

(async function init() {
  attach();
  await loadSession();
  if (state.user) await refreshAll();
  else $("#courses").innerHTML = '<div class="empty"><strong>Войдите в систему</strong>Чтобы видеть курсы, авторизуйтесь</div>';
})();