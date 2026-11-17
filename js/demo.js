/* Демо-мок API для статической версии на GitHub Pages.
   Подменяет window.fetch, подставляя заранее подготовленные данные,
   чтобы ЛМС работала без Python-бэкенда. */
(function () {
  const courses0 = [
    {
      id: 1, title: "Цифровые сервисы Ростелекома",
      description: "Как устроена экосистема компании: связь, Wink, Т2, облака, кибербезопасность и цифровые платформы.",
      category: "Введение в компанию", icon: "wifi", lessons: 2, completed: 1,
    },
    {
      id: 2, title: "Цифровые навыки и ИИ",
      description: "ИИ, промпт-инжиниринг, Python и SQL — программа развития цифровых навыков команды.",
      category: "Развитие", icon: "ai", lessons: 2, completed: 0,
    },
    {
      id: 3, title: "Корпоративное развитие и забота",
      description: "Онлайн-университет О-у, наставничество, ДМС и социальный пакет сотрудников.",
      category: "Карьера", icon: "growth", lessons: 2, completed: 0,
    },
  ];

  const lessonsByCourse = {
    1: [
      { id: 11, title: "Кто такой Ростелеком", quiz_count: 2 },
      { id: 12, title: "Связь и цифровые сервисы", quiz_count: 1 },
    ],
    2: [
      { id: 21, title: "ИИ и промпт-инжиниринг", quiz_count: 1 },
      { id: 22, title: "Python и SQL с нуля", quiz_count: 1 },
    ],
    3: [
      { id: 31, title: "Онлайн-университет О-у", quiz_count: 1 },
      { id: 32, title: "Социальный пакет и забота", quiz_count: 1 },
    ],
  };

  const lessonData = {
    11: { title: "Кто такой Ростелеком",
      content: "«Ростелеком» — ключевой в России интегрированный провайдер цифровых услуг и решений. Компания присутствует во всех сегментах рынка и обслуживает миллионы домохозяйств.\n\n• 30+ лет на рынке\n• 49 млн абонентов\n• 120 000+ сотрудников\n• 1,3 млн км оптических линий",
      quizzes: [{ id: 111, question: "Сколько примерно сотрудников работает в «Ростелекоме»?", options: ["около 20 000", "более 120 000", "около 300 000", "менее 5 000"], correct: 1 }] },
    12: { title: "Связь и цифровые сервисы",
      content: "Ключевые направления: оптические сети и интернет, умный дом, видеонаблюдение, пакетные предложения. Для развлечения — видеосервис Wink, тариф «Игровой», мобильная связь и облачное хранилище.\n\nКибербезопасность — линейка Solar. Цифровые платформы — РТ Лабс (Госуслуги), Ростелеком Лицей, Цифромед.",
      quizzes: [{ id: 121, question: "Какой сервис относится к видеосервисам Ростелекома?", options: ["Wink", "Solar", "DataLine", "Цифромед"], correct: 0 }] },
    21: { title: "ИИ и промпт-инжиниринг",
      content: "Перейти на «ты» с ИИ можно в корпоративном университете О-у: от первого знакомства до продвинутого применения. Обучение проводится на базе Нейрошлюза «Ростелекома» — сервиса с доступом к 25+ нейросетям.",
      quizzes: [{ id: 211, question: "На чём проводится обучение работе с ИИ?", options: ["Нейрошлюза Ростелекома", "обычного браузера", "облака стороннего вендора", "локального сервера"], correct: 0 }] },
    22: { title: "Python и SQL с нуля",
      content: "Программы для изучения Python и SQL с нуля онлайн, от 2,5 месяцев, несколько потоков ежегодно. В «Ростелекоме» уже более 4000 выпускников Школы.",
      quizzes: [{ id: 221, question: "Сколько выпускников уже у Школы Python и SQL?", options: ["около 400", "более 4000", "около 40 000", "ровно 1000"], correct: 1 }] },
    31: { title: "Онлайн-университет О-у",
      content: "В корпоративном университете развивают цифровые, управленческие и бизнес-навыки. • 1000+ онлайн-курсов и программ • 115+ карьерных треков • 42 часа в среднем на развитие в год.\n\nФорматы: онлайн-курсы, практикумы, коучинг и менторинг, поддержка при получении первого высшего.",
      quizzes: [{ id: 311, question: "Сколько карьерных треков доступно в О-у?", options: ["5", "45", "115+", "500+"], correct: 2 }] },
    32: { title: "Социальный пакет и забота",
      content: "Забота о сотрудниках включает ДМС, жилищную программу, негосударственное пенсионное обеспечение и кафетерий льгот: санаторное лечение, спорт, транспорт, образовательные услуги, материальная помощь к отпуску и подарки детям.",
      quizzes: [{ id: 321, question: "Что входит в социальный пакет сотрудника?", options: ["только ДМС", "ДМС, жилищная и пенсионная программы, кафетерий льгот", "только премии", "ничего из перечисленного"], correct: 1 }] },
  };

  const user = { id: 1, username: "demo", full_name: "Демо-пользователь", role: "student" };

  function respond(body) {
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(body),
    });
  }

  function match(path) {
    /* /api/courses */
    if (path === "/api/courses" || path === "/api/courses/") {
      return respond(courses0);
    }
    /* /api/courses/{id}/lessons */
    let m = path.match(/^\/api\/courses\/(\d+)\/lessons$/);
    if (m) {
      const c = courses0.find((x) => x.id === Number(m[1]));
      const lessons = (lessonsByCourse[Number(m[1])] || []).map((l, i) => ({ ...l, completed: i === 0 && Number(m[1]) === 1 }));
      return respond({ course: c, lessons });
    }
    /* /api/lessons/{id} */
    m = path.match(/^\/api\/lessons\/(\d+)$/);
    if (m) {
      const d = lessonData[Number(m[1])];
      const quizzes = d.quizzes.map((q) => ({ id: q.id, question: q.question, options: q.options }));
      return respond({ lesson: { id: Number(m[1]), title: d.title }, quizzes, progress: null });
    }
    /* /api/me */
    if (path === "/api/me") return respond(user);
    /* /api/login */
    if (path === "/api/login") return respond({ token: "demo-token", user });
    /* /api/register */
    if (path === "/api/register") return respond({ token: "demo-token", user });
    return respond({ detail: "Not found in demo" });
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    const path = url.replace(location.origin, "");
    if (path.startsWith("/api/")) return match(path);
    return realFetch(url, opts);
  };
})();