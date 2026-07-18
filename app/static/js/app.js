/**
 * UI для сервиса семейного дерева.
 */

const API = "/api/v1";

// ── JWT token handling ─────────────────────────────────────

// Менеджер JWT токенов
const jwtManager = {
  accessToken: null,
  tokenExpiry: null,

  // Инициализация из localStorage
  init() {
    const savedToken = localStorage.getItem("jwtAccessToken");
    const savedExpiry = localStorage.getItem("jwtTokenExpiry");
    if (savedToken && savedExpiry) {
      this.accessToken = savedToken;
      this.tokenExpiry = parseInt(savedExpiry, 10);
    }
  },

  // Сохранение токена
  saveToken(token, expiresIn) {
    this.accessToken = token;
    const now = Date.now();
    this.tokenExpiry = now + (expiresIn * 1000); // expiresIn в секундах
    
    localStorage.setItem("jwtAccessToken", token);
    localStorage.setItem("jwtTokenExpiry", this.tokenExpiry.toString());
    
    // Запланировать обновление за 5 минут до истечения
    const refreshIn = Math.max((expiresIn - 300) * 1000, 0);
    setTimeout(() => this.refreshTokenIfNeeded(), refreshIn);
  },

  // Проверка валидности токена
  isTokenValid() {
    if (!this.accessToken || !this.tokenExpiry) return false;
    return Date.now() < this.tokenExpiry;
  },

  // Очистка токена
  clearToken() {
    this.accessToken = null;
    this.tokenExpiry = null;
    localStorage.removeItem("jwtAccessToken");
    localStorage.removeItem("jwtTokenExpiry");
  },

  // Получение токена авторизации для заголовков
  getAuthHeader() {
    if (!this.isTokenValid()) return null;
    return `Bearer ${this.accessToken}`;
  },

  // Вход с использованием edit_token
  async loginWithEditToken(editToken) {
    try {
      const response = await api("/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edit_token: editToken.trim() }),
      });
      
      this.saveToken(response.access_token, response.expires_in);
      toastJWT("Авторизация успешна", "success");
      return true;
    } catch (e) {
      toastJWT("Неверный токен редактирования", "error");
      return false;
    }
  },

  // Обновление токена
  async refreshToken() {
    try {
      const response = await api("/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: this.accessToken }),
      });
      
      this.saveToken(response.access_token, response.expires_in);
      toastJWT("Токен обновлён", "success");
      return true;
    } catch (e) {
      console.error("Ошибка обновления токена:", e);
      // Токен истёк или недействителен
      this.clearToken();
      toastJWT("Сессия истекла. Войдите заново.", "error");
      return false;
    }
  },

  // Проверка и обновление токена при необходимости
  async refreshTokenIfNeeded() {
    if (!this.isTokenValid() || this.tokenExpiry - Date.now() < 300000) { // 5 минут
      console.log("Обновление токена...");
      return await this.refreshToken();
    }
    return true;
  }
};

// Инициализация jwtManager при загрузке
jwtManager.init();

const state = {
  people: [],
  selectedId: null,
  view: "tree", // tree | relatives | path
  depthUp: 2,
  depthDown: 2,
  editingPersonId: null, // null = create, number = edit
  isAuthenticated: jwtManager.isTokenValid(),
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const el = {
  personList: $("#person-list"),
  search: $("#search"),
  toggleSidebar: $("#btn-toggle-sidebar"),
  sidebar: $(".sidebar"),
  viewArea: $("#view-area"),
  empty: $("#empty-state"),
  canvas: $("#tree-canvas"),
  pathResult: $("#path-result"),
  detail: $("#detail-panel"),
  detailName: $("#detail-name"),
  detailSub: $("#detail-sub"),
  detailRel: $("#detail-relatives"),
  toolbarHint: $("#toolbar-hint"),
  depthUp: $("#depth-up"),
  depthDown: $("#depth-down"),
  toasts: $("#toasts"),
};

// ── API helpers ────────────────────────────────────────

async function api(path, options = {}) {
  const headers = options.headers || {};
  // Не устанавливать Content-Type для FormData — браузер сам multipart/form-data с boundary
  const body = options.body;
  if (!(body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  // Добавить токен редактирования, если он есть (только при запросах, где предполагается изменение)
  // Но здесь не знаем, изменяется ли или нет. Будем добавлять токен только в явно edit-запросах на клиенте.
  const res = await fetch(`${API}${path}`, {
    headers,
    ...options,
  });
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : `Ошибка ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// Уведомления для JWT (избегаем конфликта с другими функциями toast)
function toastJWT(message, type = "success") {
  const existingToast = document.querySelector(".jwt-toast");
  if (existingToast) existingToast.remove();

  const node = document.createElement("div");
  node.className = `toast jwt-toast ${type}`;
  node.textContent = message;
  node.style.position = "fixed";
  node.style.top = "20px";
  node.style.left = "50%";
  node.style.transform = "translateX(-50%)";
  node.style.zIndex = "9999";
  document.body.appendChild(node);
  
  setTimeout(() => node.remove(), 3000);
}

// Обновленная функция обеспечения JWT токена
async function ensureJWToken() {
  if (jwtManager.isTokenValid()) {
    return await jwtManager.refreshTokenIfNeeded();
  }
  
  const editToken = prompt(
    "Введите токен редактирования (указан в конфиге службы, по умолчанию 'secret')"
  );
  if (editToken === null) return false;
  const trimmed = editToken.trim();
  if (!trimmed) return false;
  
  return await jwtManager.loginWithEditToken(trimmed);
}

// Обновленная функция API с JWT
async function apiWithJWT(path, options = {}) {
  if (!(await ensureJWToken())) {
    throw new Error("Требуется авторизация");
  }
  
  const headers = (options.headers = options.headers || {});
  const authHeader = jwtManager.getAuthHeader();
  if (authHeader) {
    headers["Authorization"] = authHeader;
  }
  
  return api(path, options);
}

// ── Functions for fetch with JWT token (FormData support without JSON parsing) ----------
async function fetchWithJWT(url, init = {}) {
  if (!(await ensureJWToken())) {
    throw new Error("Требуется авторизация");
  }
  init = init || {};
  const headers = new Headers(init.headers || {});
  const authHeader = jwtManager.getAuthHeader();
  if (authHeader) {
    headers.set("Authorization", authHeader);
  }
  init.headers = headers;
  const res = await fetch(url, init);
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : `Ошибка ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ── UI helpers ─────────────────────────────────────────

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  el.toasts.appendChild(node);
  setTimeout(() => node.remove(), 3500);
}

function openModal(id) {
  $(`#${id}`).classList.remove("hidden");
}

function closeModal(id) {
  $(`#${id}`).classList.add("hidden");
}

function initials(person) {
  const a = (person.first_name || "?")[0];
  const b = (person.last_name || "")[0] || "";
  return (a + b).toUpperCase();
}

function avatarHtml(person, extraClass = "avatar") {
  const g = genderClass(person.gender);
  if (person.photo_url) {
    return `<div class="${extraClass} ${g} has-photo"><img src="${escapeHtml(person.photo_url)}" alt="" loading="lazy" /></div>`;
  }
  return `<div class="${extraClass} ${g}">${escapeHtml(initials(person))}</div>`;
}

function formatDates(person) {
  const b = person.birth_date ? person.birth_date.slice(0, 4) : "?";
  if (person.death_date) return `${b} – ${person.death_date.slice(0, 4)}`;
  return b !== "?" ? `р. ${b}` : "";
}

function genderClass(g) {
  return g || "unknown";
}

function personOptions(selectEl, selectedId = null) {
  selectEl.innerHTML = state.people
    .map(
      (p) =>
        `<option value="${p.id}" ${p.id === selectedId ? "selected" : ""}>${escapeHtml(p.full_name)} (#${p.id})</option>`
    )
    .join("");
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ── People list ────────────────────────────────────────

async function loadPeople(q = "") {
  const query = q ? `?q=${encodeURIComponent(q)}` : "";
  state.people = await api(`/persons${query}`);
  renderPersonList();
  fillRelationSelects();
}

function renderPersonList() {
  if (!state.people.length) {
    el.personList.innerHTML =
      '<p style="padding:1rem;color:var(--text-muted);font-size:0.85rem">Пока никого нет.<br>Добавьте первого человека.</p>';
    return;
  }

  el.personList.innerHTML = state.people
    .map((p) => {
      const active = p.id === state.selectedId ? "active" : "";
      return `
        <div class="person-item ${active}" data-id="${p.id}" role="button" tabindex="0">
          ${avatarHtml(p, "avatar")}
          <div class="person-meta">
            <div class="name">${escapeHtml(p.full_name)}</div>
            <div class="sub">${escapeHtml(formatDates(p) || "даты неизвестны")}</div>
          </div>
        </div>`;
    })
    .join("");
}

function fillRelationSelects() {
  personOptions($("#r-person"), state.selectedId);
  personOptions($("#r-related"));
  personOptions($("#path-from"), state.selectedId);
  personOptions($("#path-to"));
}

// ── Selection & views ──────────────────────────────────

async function selectPerson(id, { openDetail = false } = {}) {
  state.selectedId = id;
  renderPersonList();
  el.toolbarHint.textContent = "";
  await renderMainView();
  if (openDetail) await showDetail(id);
}

async function renderMainView() {
  if (!state.selectedId && state.view !== "path") {
    el.empty.classList.remove("hidden");
    el.canvas.classList.add("hidden");
    el.pathResult.classList.add("hidden");
    return;
  }

  el.empty.classList.add("hidden");

  if (state.view === "path") {
    // path result managed separately
    return;
  }

  el.pathResult.classList.add("hidden");
  el.canvas.classList.remove("hidden");

  state.depthUp = Number(el.depthUp.value) || 0;
  state.depthDown = Number(el.depthDown.value) || 0;

  if (state.view === "tree") {
    const tree = await api(
      `/tree/${state.selectedId}?depth_up=${state.depthUp}&depth_down=${state.depthDown}`
    );
    renderTree(tree);
    el.toolbarHint.textContent = `Узлов: ${tree.nodes_count}`;
  } else {
    const rel = await api(`/tree/${state.selectedId}/relatives`);
    renderRelativesView(rel);
    el.toolbarHint.textContent = "Ближайшие родственники";
  }
}

// ── Tree rendering ─────────────────────────────────────

/**
 * Собираем уровни: предки (отрицательные поколения), корень (0), потомки (положительные).
 */
function collectLevels(node) {
  const levels = new Map(); // gen -> Map(id -> {person, spouses, isRoot})

  function walkUp(n) {
    const gen = n.person.generation ?? 0;
    if (!levels.has(gen)) levels.set(gen, new Map());
    const map = levels.get(gen);
    if (!map.has(n.person.id)) {
      map.set(n.person.id, {
        person: n.person,
        spouses: n.spouses || [],
        isRoot: gen === 0,
      });
    }
    for (const p of n.parents || []) walkUp(p);
  }

  function walkDown(n) {
    for (const c of n.children || []) {
      const gen = c.person.generation ?? 0;
      if (!levels.has(gen)) levels.set(gen, new Map());
      const map = levels.get(gen);
      if (!map.has(c.person.id)) {
        map.set(c.person.id, {
          person: c.person,
          spouses: c.spouses || [],
          isRoot: false,
        });
      }
      walkDown(c);
    }
  }

  walkUp(node);
  walkDown(node);
  return levels;
}

function cardHtml(entry) {
  const p = entry.person;
  const rootCls = entry.isRoot ? "root" : "";
  const spouses =
    entry.spouses && entry.spouses.length
      ? `<div class="spouses">♥ ${entry.spouses.map((s) => escapeHtml(s.full_name)).join(", ")}</div>`
      : "";
  return `
    <div class="person-card ${rootCls}" data-id="${p.id}">
      ${avatarHtml(p, "card-avatar")}
      <div class="card-name">${escapeHtml(p.full_name)}</div>
      <div class="card-dates">${escapeHtml(formatDates(p))}</div>
      ${spouses}
    </div>`;
}

function renderTree(treeData) {
  const levels = collectLevels(treeData.tree);
  const gens = [...levels.keys()].sort((a, b) => a - b);

  const genTitle = (g) => {
    if (g === 0) return "Выбранный человек";
    if (g < 0) return g === -1 ? "Родители" : `Предки (${Math.abs(g)} пок.)`;
    if (g === 1) return "Дети";
    if (g === 2) return "Внуки";
    return `Потомки (+${g})`;
  };

  let html = "";
  gens.forEach((g, i) => {
    const entries = [...levels.get(g).values()];
    html += `<div class="generation">`;
    if (i > 0) html += `<div class="connector"></div>`;
    html += `<div class="gen-label">${genTitle(g)}</div>`;
    html += `<div class="gen-row">${entries.map(cardHtml).join("")}</div>`;
    html += `</div>`;
  });

  el.canvas.innerHTML = html || "<p style='color:var(--text-muted)'>Нет данных для отображения</p>";
}

function renderRelativesView(rel) {
  const groups = [
    ["Родители", rel.parents],
    ["Супруги", rel.spouses],
    ["Дети", rel.children],
    ["Братья и сёстры", rel.siblings],
  ];

  const root = state.people.find((p) => p.id === state.selectedId);
  let html = `
    <div class="generation">
      <div class="gen-label">Центр</div>
      <div class="gen-row">
        ${cardHtml({ person: root || { id: state.selectedId, full_name: "?", first_name: "?", gender: "unknown" }, spouses: [], isRoot: true })}
      </div>
    </div>`;

  for (const [title, list] of groups) {
    html += `<div class="connector"></div>`;
    html += `<div class="generation"><div class="gen-label">${title}</div><div class="gen-row">`;
    if (!list.length) {
      html += `<p style="color:var(--text-muted);font-size:0.85rem">нет данных</p>`;
    } else {
      html += list.map((p) => cardHtml({ person: p, spouses: [], isRoot: false })).join("");
    }
    html += `</div></div>`;
  }

  el.canvas.innerHTML = html;
}

// ── Detail panel ───────────────────────────────────────

async function showDetail(id) {
  const person = state.people.find((p) => p.id === id) || (await api(`/persons/${id}`));
  const rel = await api(`/tree/${id}/relatives`);

  el.detailName.textContent = person.full_name;
  const bits = [formatDates(person), person.birth_place, genderLabel(person.gender)].filter(Boolean);
  el.detailSub.textContent = bits.join(" · ");

  // фото в панели
  let photoBlock = el.detail.querySelector(".detail-photo");
  if (!photoBlock) {
    photoBlock = document.createElement("div");
    photoBlock.className = "detail-photo";
    el.detailSub.insertAdjacentElement("afterend", photoBlock);
  }
  if (person.photo_url) {
    photoBlock.innerHTML = `<img src="${escapeHtml(person.photo_url)}" alt="${escapeHtml(person.full_name)}" />`;
    photoBlock.classList.remove("hidden");
  } else {
    photoBlock.innerHTML = "";
    photoBlock.classList.add("hidden");
  }

  const groups = [
    ["Родители", rel.parents],
    ["Супруги", rel.spouses],
    ["Дети", rel.children],
    ["Братья / сёстры", rel.siblings],
  ];

  el.detailRel.innerHTML = groups
    .map(([title, list]) => {
      if (!list.length) return "";
      const chips = list
        .map(
          (p) =>
            `<span class="rel-chip" data-id="${p.id}">${escapeHtml(p.full_name)}</span>`
        )
        .join("");
      return `<div class="rel-group"><h4>${title}</h4>${chips}</div>`;
    })
    .join("") || '<p style="margin-top:0.75rem;color:var(--text-muted);font-size:0.85rem">Родственники не указаны</p>';

  el.detail.dataset.personId = String(id);
  el.detail.classList.remove("hidden");
}

function genderLabel(g) {
  return { male: "муж.", female: "жен.", other: "др.", unknown: "" }[g] || "";
}

// ── Forms ──────────────────────────────────────────────

function emptyToNull(v) {
  return v === "" || v === undefined ? null : v;
}

function openPersonModalCreate() {
  state.editingPersonId = null;
  const form = $("#form-person");
  form.reset();
  $("#p-id").value = "";
  $("#modal-person-title").textContent = "Новый человек";
  $("#btn-person-submit").textContent = "Создать";
  // фото секция в create — скрыта (нельзя загрузить до создания, но можно потом в edit)
  $("#modal-photo-section").classList.add("hidden");
  openModal("modal-person");
  $("#p-first").focus();
}

async function openPersonModalEdit(id) {
  let person = state.people.find((p) => p.id === id);
  if (!person) {
    person = await api(`/persons/${id}`);
  }
  state.editingPersonId = id;
  $("#modal-person-title").textContent = `Редактирование: ${person.full_name}`;
  $("#btn-person-submit").textContent = "Сохранить";
  $("#p-id").value = String(id);
  $("#p-first").value = person.first_name || "";
  $("#p-last").value = person.last_name || "";
  $("#p-middle").value = person.middle_name || "";
  $("#p-maiden").value = person.maiden_name || "";
  $("#p-gender").value = person.gender || "unknown";
  $("#p-birth").value = person.birth_date || "";
  $("#p-death").value = person.death_date || "";
  $("#p-birth-place").value = person.birth_place || "";
  $("#p-notes").value = person.notes || "";
  // фото
  const photoSection = $("#modal-photo-section");
  const photoPreview = $("#photo-preview");
  const photoImg = $("#photo-preview-img");
  const placeholder = $("#photo-placeholder");
  const delBtn = $("#btn-delete-photo");
  if (person.photo_url) {
    photoImg.src = person.photo_url;
    photoPreview.style.display = "block";
    placeholder.style.display = "none";
    delBtn.style.display = "inline-flex";
    $("#p-photo-path").value = person.photo_url || "";
    photoSection.classList.remove("hidden");
  } else {
    photoPreview.style.display = "none";
    placeholder.style.display = "block";
    delBtn.style.display = "none";
    $("#p-photo-path").value = "";
    // показываем секцию, даже если фото нет
    photoSection.classList.remove("hidden");
  }
  openModal("modal-person");
  $("#p-first").focus();
}

function personFormBody(fd) {
  return {
    first_name: fd.get("first_name").trim(),
    last_name: emptyToNull(fd.get("last_name")?.trim()),
    middle_name: emptyToNull(fd.get("middle_name")?.trim()),
    maiden_name: emptyToNull(fd.get("maiden_name")?.trim()),
    gender: fd.get("gender") || "unknown",
    birth_date: emptyToNull(fd.get("birth_date")),
    death_date: emptyToNull(fd.get("death_date")),
    birth_place: emptyToNull(fd.get("birth_place")?.trim()),
    notes: emptyToNull(fd.get("notes")?.trim()),
  };
}

async function onSavePerson(e) {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const body = personFormBody(fd);
  const editId = state.editingPersonId;

  try {
    let person;
    if (editId) {
      person = await apiWithJWT(`/persons/${editId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      toast(`Сохранено: ${person.full_name}`);
      // обновить превью, если изменилось (при обновлении фото_path)
      const savedUrl = $("#p-photo-path").value;
      if (savedUrl) {
        const photoImg = $("#photo-preview-img");
        photoImg.src = savedUrl;
        $("#photo-preview").style.display = "block";
        $("#photo-placeholder").style.display = "none";
        $("#btn-delete-photo").style.display = "inline-flex";
      } else {
        $("#photo-preview").style.display = "none";
        $("#photo-placeholder").style.display = "block";
        $("#btn-delete-photo").style.display = "none";
      }
    } else {
      person = await apiWithJWT("/persons", { method: "POST", body: JSON.stringify(body) });
      toast(`Добавлен: ${person.full_name}`);
    }
    closeModal("modal-person");
    form.reset();
    state.editingPersonId = null;
    await loadPeople(el.search.value.trim());
    await selectPerson(person.id, { openDetail: true });
  } catch (err) {
    toast(err.message, "error");
  }
}

async function onCreateRelation(e) {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const body = {
    person_id: Number(fd.get("person_id")),
    related_person_id: Number(fd.get("related_person_id")),
    relation_type: fd.get("relation_type"),
  };

  try {
    await apiWithJWT("/relationships", { method: "POST", body: JSON.stringify(body) });
    toast("Связь создана");
    closeModal("modal-relation");
    form.reset();
    if (state.selectedId) await renderMainView();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function onFindPath(e) {
  e.preventDefault();
  const fromId = Number($("#path-from").value);
  const toId = Number($("#path-to").value);

  try {
    const data = await api(`/tree/path/${fromId}/${toId}`);
    closeModal("modal-path");
    state.view = "path";
    $$("#view-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "tree"));
    el.empty.classList.add("hidden");
    el.canvas.classList.add("hidden");
    el.detail.classList.add("hidden");
    el.pathResult.classList.remove("hidden");

    const stepsHtml =
      data.steps.length === 0
        ? "<li>Это один и тот же человек</li>"
        : data.steps
            .map(
              (s, i) =>
                `<li><span class="step-num">${i + 1}</span><span>${escapeHtml(s)}</span></li>`
            )
            .join("");

    const pathPeople = data.path
      .map(
        (p) =>
          `<span class="rel-chip" data-id="${p.id}" style="cursor:pointer">${escapeHtml(p.full_name)}</span>`
      )
      .join(" → ");

    el.pathResult.innerHTML = `
      <h3>Путь найден (${data.path.length} чел.)</h3>
      <p style="margin-bottom:1rem;line-height:1.8">${pathPeople}</p>
      <ul class="path-steps">${stepsHtml}</ul>
      <div style="margin-top:1rem">
        <button class="btn btn-sm" type="button" id="btn-back-tree">← К дереву</button>
      </div>`;

    el.toolbarHint.textContent = `Путь: ${data.path[0]?.full_name} → ${data.path.at(-1)?.full_name}`;
    toast("Путь найден");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function onExportObsidian() {
  if (!confirm("Экспортировать всех людей в Markdown для Obsidian?")) return;
  try {
    const data = await api("/export/obsidian", { method: "POST" });
    toast(data.message || "Экспорт готов");
    if (data.warnings?.length) console.warn(data.warnings);

    const wantZip = confirm(
      `Готово: ${data.people} чел.\n\nПапка:\n${data.export_dir}\n\nСкачать ZIP?`
    );
    if (!wantZip) return;

    const res = await fetch(`${API}/export/obsidian/zip`);
    if (!res.ok) {
      let msg = `Ошибка ${res.status}`;
      try {
        const j = await res.json();
        msg = j.detail || msg;
      } catch {
        /* ignore */
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "family_tree_obsidian.zip";
    a.click();
    URL.revokeObjectURL(url);
    toast("ZIP скачан");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function onImportGramps(e) {
  const input = e.target;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;

  const proceed = confirm(`Импортировать файл «${file.name}» из Gramps?`);
  if (!proceed) return;

  const replace = confirm(
    "Заменить текущее дерево?\n\nOK — очистить базу и импортировать\nОтмена — добавить к существующим данным"
  );

  const form = new FormData();
  form.append("file", file);

  try {
    const data = await fetchWithJWT(`${API}/import/gramps?replace=${replace ? "true" : "false"}`, {
      method: "POST",
      body: form,
    });
    toast(data.message || "Импорт завершён");
    if (data.warnings?.length) {
      console.warn("Gramps import warnings:", data.warnings);
      toast(`Предупреждений: ${data.warnings.length} (см. консоль)`, "error");
    }
    state.selectedId = null;
    el.detail.classList.add("hidden");
    await loadPeople(el.search.value.trim());
    if (state.people.length) {
      await selectPerson(state.people[0].id);
    } else {
      el.canvas.classList.add("hidden");
      el.empty.classList.remove("hidden");
    }
  } catch (err) {
    toast(err.message, "error");
  }
}

async function deleteSelectedPerson() {
  const id = Number(el.detail.dataset.personId);
  if (!id) return;
  const person = state.people.find((p) => p.id === id);
  if (!confirm(`Удалить «${person?.full_name || id}» и все его связи?`)) return;

  try {
    await apiWithJWT(`/persons/${id}`, { method: "DELETE" });
    toast("Человек удалён");
    el.detail.classList.add("hidden");
    if (state.selectedId === id) {
      state.selectedId = null;
      el.canvas.classList.add("hidden");
      el.empty.classList.remove("hidden");
    }
    await loadPeople(el.search.value.trim());
  } catch (err) {
    toast(err.message, "error");
  }
}

// ── Photo handling ────────────────────────────────────────

async function onPhotoFileSelected(e) {
  const file = e.target.files[0];
  if (!file) return;
  const editId = state.editingPersonId;
  if (!editId) {
    toast("Фото можно загрузить после создания человека (через редактирование)", "error");
    return;
  }
  // размер
  if (file.size > 10 * 1024 * 1024) {
    toast("Файл слишком большой (макс. 10 МБ)", "error");
    return;
  }
  // тип
  const type = file.type;
  if (
    !(
      type.startsWith("image/") &&
      ["jpeg", "png", "gif", "webp", "tiff", "bmp"].some((t) => type.includes(t))
    )
  ) {
    toast("Поддерживаются только изображения (JPEG, PNG, GIF, WebP, TIFF, BMP)", "error");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const media = await apiWithJWT(`/persons/${editId}/photo?is_primary=true`, {
      method: "POST",
      body: form,
    });
    toast("Фото загружено");
    // показать превью
    const photoImg = $("#photo-preview-img");
    photoImg.src = media.url;
    $("#photo-preview").style.display = "block";
    $("#photo-placeholder").style.display = "none";
    $("#btn-delete-photo").style.display = "inline-flex";
    $("#p-photo-path").value = media.url;
  } catch (err) {
    toast(err.message || "Не удалось загрузить фото", "error");
  }
  e.target.value = "";
}

async function onDeletePhoto() {
  const editId = state.editingPersonId;
  if (!editId) return;
  if (!confirm("Удалить фото?")) return;
  try {
    await apiWithJWT(`/persons/${editId}/photo/all`, { method: "DELETE" });
    toast("Фото удалено");
    // скрыть превью
    $("#photo-preview").style.display = "none";
    $("#photo-placeholder").style.display = "block";
    $("#btn-delete-photo").style.display = "none";
    $("#p-photo-path").value = "";
    $("#photo-preview-img").src = "";
  } catch (err) {
    toast(err.message || "Не удалось удалить фото", "error");
  }
}

// ── Relation type labels ───────────────────────────────

function updateRelationLabels() {
  const type = $("#r-type").value;
  if (type === "parent_of") {
    $("#r-person-label").textContent = "Родитель";
    $("#r-related-label").textContent = "Ребёнок";
  } else {
    $("#r-person-label").textContent = "Супруг(а) 1";
    $("#r-related-label").textContent = "Супруг(а) 2";
  }
}

// ── Events ─────────────────────────────────────────────

function bindEvents() {
  let searchTimer;
  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadPeople(el.search.value.trim()), 250);
  });

  el.personList.addEventListener("click", (e) => {
    const item = e.target.closest(".person-item");
    if (item) selectPerson(Number(item.dataset.id), { openDetail: true });
  });

  // Add touch support for mobile devices
  el.personList.addEventListener("touchend", (e) => {
    const item = e.target.closest(".person-item");
    if (item) {
      e.preventDefault(); // Prevent click event from firing
      selectPerson(Number(item.dataset.id), { openDetail: true });
    }
  });

  el.personList.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const item = e.target.closest(".person-item");
    if (item) selectPerson(Number(item.dataset.id), { openDetail: true });
  });

  el.canvas.addEventListener("click", (e) => {
    const card = e.target.closest(".person-card");
    if (card) showDetail(Number(card.dataset.id));
  });

  // Add touch support for tree cards
  el.canvas.addEventListener("touchend", (e) => {
    const card = e.target.closest(".person-card");
    if (card) {
      e.preventDefault(); // Prevent click event from firing
      showDetail(Number(card.dataset.id));
    }
  });

  el.detailRel.addEventListener("click", (e) => {
    const chip = e.target.closest(".rel-chip");
    if (chip) selectPerson(Number(chip.dataset.id), { openDetail: true });
  });

  el.pathResult.addEventListener("click", (e) => {
    if (e.target.id === "btn-back-tree") {
      state.view = "tree";
      $$("#view-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "tree"));
      el.pathResult.classList.add("hidden");
      if (state.selectedId) renderMainView();
      else {
        el.empty.classList.remove("hidden");
      }
      return;
    }
    const chip = e.target.closest(".rel-chip");
    if (chip) selectPerson(Number(chip.dataset.id), { openDetail: true });
  });

  $("#btn-close-detail").addEventListener("click", () => el.detail.classList.add("hidden"));
  $("#btn-set-root").addEventListener("click", () => {
    const id = Number(el.detail.dataset.personId);
    if (id) {
      state.view = "tree";
      $$("#view-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "tree"));
      selectPerson(id);
    }
  });
  $("#btn-edit-person").addEventListener("click", () => {
    const id = Number(el.detail.dataset.personId);
    if (id) openPersonModalEdit(id).catch((e) => toast(e.message, "error"));
  });
  $("#btn-delete-person").addEventListener("click", deleteSelectedPerson);

  $("#btn-add-person").addEventListener("click", openPersonModalCreate);
  $("#btn-upload-photo").addEventListener("click", () => $("#photo-file-input").click());
  $("#photo-file-input").addEventListener("change", onPhotoFileSelected);
  $("#btn-delete-photo").addEventListener("click", onDeletePhoto);
  $("#btn-add-relation").addEventListener("click", () => {
    fillRelationSelects();
    updateRelationLabels();
    openModal("modal-relation");
  });
  $("#btn-import").addEventListener("click", () => $("#file-gramps").click());
  $("#file-gramps").addEventListener("change", onImportGramps);
  $("#btn-export-obsidian").addEventListener("click", onExportObsidian);
  $("#btn-path").addEventListener("click", () => {
    fillRelationSelects();
    openModal("modal-path");
  });
  $("#btn-refresh").addEventListener("click", () => {
    if (state.selectedId) renderMainView().catch((e) => toast(e.message, "error"));
  });

  el.depthUp.addEventListener("change", () => {
    if (state.selectedId && state.view === "tree") renderMainView();
  });
  el.depthDown.addEventListener("change", () => {
    if (state.selectedId && state.view === "tree") renderMainView();
  });

  $$("#view-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      $$("#view-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
      el.pathResult.classList.add("hidden");
      if (state.selectedId) renderMainView().catch((e) => toast(e.message, "error"));
    });
  });

  $("#form-person").addEventListener("submit", onSavePerson);
  $("#btn-upload-photo").addEventListener("click", () => $("#photo-file-input").click());
  $("#photo-file-input").addEventListener("change", onPhotoFileSelected);
  $("#btn-delete-photo").addEventListener("click", onDeletePhoto);
  $("#form-relation").addEventListener("submit", onCreateRelation);
  $("#form-path").addEventListener("submit", onFindPath);
  $("#r-type").addEventListener("change", updateRelationLabels);

  $$("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });

  $$(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.add("hidden");
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $$(".modal-overlay:not(.hidden)").forEach((m) => m.classList.add("hidden"));
    }
  });

  // Mobile sidebar toggle
  const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
  const sidebar = document.querySelector(".sidebar");

  if (btnToggleSidebar && sidebar) {
    let isCollapsed = false;

    // Don't collapse by default even on mobile
    btnToggleSidebar.addEventListener("click", () => {
      isCollapsed = !isCollapsed;
      sidebar.classList.toggle("collapsed", isCollapsed);
      btnToggleSidebar.style.opacity = isCollapsed ? "0.6" : "1";
    });
  }

  // Auth UI
  const authStatus = document.getElementById("auth-status");
  const btnAuth = document.getElementById("btn-auth");
  
  function updateAuthUI() {
    const isAuthenticated = jwtManager.isTokenValid();
    state.isAuthenticated = isAuthenticated;
    
    if (authStatus) {
      if (isAuthenticated) {
        authStatus.innerHTML = `<span class="auth-indicator auth-indicator--active"></span>`;
        authStatus.title = "Авторизован";
      } else {
        authStatus.innerHTML = `<span class="auth-indicator auth-indicator--inactive"></span>`;
        authStatus.title = "Не авторизован";
      }
    }
    
    if (btnAuth) {
      btnAuth.textContent = isAuthenticated ? "🚪" : "🔐";
      btnAuth.title = isAuthenticated ? "Выйти" : "Войти";
    }
  }
  
  async function handleAuthClick() {
    const isAuthenticated = jwtManager.isTokenValid();
    
    if (isAuthenticated) {
      // Выход
      if (confirm("Выйти из системы?")) {
        jwtManager.clearToken();
        updateAuthUI();
        toastJWT("Вы вышли из системы", "success");
      }
    } else {
      // Вход
      const editToken = prompt("Введите токен редактирования:");
      if (editToken === null) return;
      
      const success = await jwtManager.loginWithEditToken(editToken.trim());
      if (success) {
        updateAuthUI();
      }
    }
  }
  
  if (btnAuth) {
    btnAuth.addEventListener("click", handleAuthClick);
  }
  
  // Обновляем UI при загрузке
  updateAuthUI();
  
  // Следим за изменением статуса токена
  setInterval(updateAuthUI, 60000); // Каждую минуту
  
  // Добавляем CSS для индикаторов авторизации
  const style = document.createElement("style");
  style.textContent = `
    .auth-section {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    
    .auth-status {
      display: flex;
      align-items: center;
    }
    
    .auth-indicator {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
    }
    
    .auth-indicator--active {
      background-color: #4CAF50;
      box-shadow: 0 0 4px #4CAF50;
    }
    
    .auth-indicator--inactive {
      background-color: #f44336;
      box-shadow: 0 0 4px #f44336;
    }
  `;
  document.head.appendChild(style);
}

// ── Init ───────────────────────────────────────────────

async function init() {
  bindEvents();
  try {
    await loadPeople();
    if (state.people.length) {
      // Prefer demo root "Сергей" if present
      const sergey = state.people.find((p) => p.first_name === "Сергей");
      await selectPerson((sergey || state.people[0]).id);
    }
  } catch (err) {
    toast(`Не удалось загрузить данные: ${err.message}`, "error");
  }
}

init();
