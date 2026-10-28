/**
 * UI для сервиса семейного дерева.
 */

const API = (window.ROOT_PATH || "") + "/api/v1";

// ── Session (JWT в httpOnly cookie, JS хранит только role/expiry) ─────────

const jwtManager = {
  tokenExpiry: null,
  userRole: null, // "viewer" | "editor"
  _refreshTimer: null,

  /** Убрать старые небезопасные ключи localStorage (миграция). */
  _purgeLegacyStorage() {
    try {
      localStorage.removeItem("jwtAccessToken");
      localStorage.removeItem("jwtTokenExpiry");
      localStorage.removeItem("jwtUserRole");
    } catch {
      /* ignore */
    }
  },

  /**
   * Восстановить сессию по cookie через /auth/me.
   * Токен в JS не хранится — только httpOnly cookie.
   */
  async init() {
    this._purgeLegacyStorage();
    try {
      const res = await fetch(`${API}/auth/me`, { credentials: "include" });
      if (!res.ok) {
        this.clearSession(false);
        return false;
      }
      const data = await res.json();
      this.userRole = data.role || null;
      this.isAdmin = !!data.is_admin;
      // exp — unix seconds из JWT
      this.tokenExpiry = data.exp ? data.exp * 1000 : null;
      if (typeof state !== "undefined") {
        state.userRole = this.userRole;
        state.isAdmin = !!this.isAdmin;
        state.isAuthenticated = this.isTokenValid();
      }
      this._scheduleRefresh();
      return this.isTokenValid();
    } catch {
      this.clearSession(false);
      return false;
    }
  },

  /** Сохранить метаданные сессии после login/refresh (токен только в cookie). */
  saveSession(expiresIn, role) {
    this.userRole = role;
    if (typeof state !== "undefined") state.userRole = role;
    this.tokenExpiry = Date.now() + expiresIn * 1000;
    this._scheduleRefresh();
  },

  isTokenValid() {
    if (!this.userRole || !this.tokenExpiry) return false;
    return Date.now() < this.tokenExpiry;
  },

  clearSession(callLogout = true) {
    this.tokenExpiry = null;
    this.userRole = null;
    if (typeof state !== "undefined") state.userRole = null;
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    this._purgeLegacyStorage();
    if (callLogout) {
      fetch(`${API}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});
    }
  },

  /** @deprecated clearToken — алиас clearSession */
  clearToken() {
    this.clearSession(true);
  },

  /** Медиа URL без токена в query — cookie уходит same-origin автоматически.
   *  window.ROOT_PATH подставляется при деплое за префиксом reverse-proxy. */
  mediaUrl(url) {
    if (!url) return url;
    const root = window.ROOT_PATH || "";
    if (root && url.startsWith("/")) return root + url;
    return url;
  },

  _scheduleRefresh() {
    if (this._refreshTimer) clearTimeout(this._refreshTimer);
    if (!this.tokenExpiry) return;
    const msLeft = this.tokenExpiry - Date.now();
    const refreshIn = Math.max(msLeft - 5 * 60 * 1000, 10_000);
    this._refreshTimer = setTimeout(() => this.refreshTokenIfNeeded(), refreshIn);
  },

  async loginWithToken(token) {
    try {
      // Сырой fetch: не через api() — иначе 401 откроет модалку повторно
      const res = await fetch(`${API}/auth/token`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      if (!res.ok) return false;
      const response = await res.json();
      this.saveSession(response.expires_in, response.role);
      const roleText = response.role === "editor" ? "редактирование" : "просмотр";
      toastJWT(`✓ Авторизация успешна (${roleText})`, "success");
      return true;
    } catch {
      return false;
    }
  },

  async refreshToken() {
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) throw new Error("refresh failed");
      const response = await res.json();
      this.saveSession(response.expires_in, response.role);
      return true;
    } catch (e) {
      console.error("Ошибка обновления токена:", e);
      this.clearSession(false);
      toastJWT("Сессия истекла. Войдите заново.", "error");
      return false;
    }
  },

  async refreshTokenIfNeeded() {
    if (!this.isTokenValid() || this.tokenExpiry - Date.now() < 300000) {
      return await this.refreshToken();
    }
    return true;
  },
};

/** Открыть модалку входа; resolve(true/false) после попытки. */
function openAuthModal({ preferEditor = false } = {}) {
  return new Promise((resolve) => {
    const form = $("#form-auth");
    const levelEl = $("#auth-level");
    const tokenEl = $("#auth-token");
    const overlay = $("#modal-auth");
    if (!form || !levelEl || !tokenEl || !overlay) {
      resolve(false);
      return;
    }

    // Если модалка уже открыта с ожиданием — не плодим промисы
    if (overlay.dataset.authPending === "1") {
      resolve(false);
      return;
    }
    overlay.dataset.authPending = "1";

    levelEl.value = preferEditor ? "editor" : "viewer";
    tokenEl.value = "";
    updateAuthTokenHint();
    openModal("modal-auth");
    setTimeout(() => tokenEl.focus(), 50);

    let settled = false;
    const cancelBtns = $$('[data-close="modal-auth"]');

    const cleanup = () => {
      form.removeEventListener("submit", onSubmit);
      cancelBtns.forEach((b) => b.removeEventListener("click", onCancel));
      overlay.removeEventListener("click", onOverlay);
      document.removeEventListener("keydown", onKey);
      delete overlay.dataset.authPending;
    };

    const finish = (ok) => {
      if (settled) return;
      settled = true;
      cleanup();
      closeModal("modal-auth");
      resolve(ok);
    };

    const onCancel = () => finish(false);
    const onOverlay = (e) => {
      if (e.target === overlay) finish(false);
    };
    const onKey = (e) => {
      // finish даже если другой обработчик уже скрыл оверлей
      if (e.key === "Escape" && overlay.dataset.authPending === "1") finish(false);
    };
    const onSubmit = async (e) => {
      e.preventDefault();
      const trimmed = tokenEl.value.trim();
      if (!trimmed) {
        toast("Токен не может быть пустым", "error");
        return;
      }
      const success = await jwtManager.loginWithToken(trimmed);
      if (success) {
        state.isAuthenticated = true;
        state.authRetryCount = 0;
        finish(true);
        loadPeople();
        if (typeof window.__updateAuthUI === "function") window.__updateAuthUI();
      } else {
        toast("Неверный токен авторизации", "error");
        tokenEl.focus();
        tokenEl.select();
      }
    };

    form.addEventListener("submit", onSubmit);
    cancelBtns.forEach((b) => b.addEventListener("click", onCancel));
    overlay.addEventListener("click", onOverlay);
    document.addEventListener("keydown", onKey);
  });
}

function updateAuthTokenHint() {
  const levelEl = $("#auth-level");
  const hintEl = $("#auth-token-hint");
  if (!levelEl || !hintEl) return;
  hintEl.textContent =
    levelEl.value === "editor"
      ? "Токен редактирования (edit token из .env)"
      : "Токен просмотра (view token из .env)";
}

// Глобальный вход (кнопка в пустом списке и т.п.)
window.requestAuth = async function () {
  state.authRetryCount = 0;
  return openAuthModal();
};

const state = {
  people: [],
  selectedId: null,
  view: "tree", // tree | relatives | path
  depthUp: 2,
  depthDown: 2,
  editingPersonId: null, // null = create, number = edit
  isAuthenticated: false,
  userRole: null, // "viewer" or "editor"
  authRetryCount: 0, // Счетчик неудачных попыток авторизации
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const el = {
  personList: $("#person-list"),
  search: $("#search"),
  toggleSidebar: $("#btn-toggle-sidebar"),
  closeSidebar: $("#btn-close-sidebar"),
  sidebar: $("#sidebar") || $(".sidebar"),
  sidebarBackdrop: $("#sidebar-backdrop"),
  app: $(".app"),
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
  headerMore: $("#btn-header-more"),
  headerMenuPanel: $("#header-menu-panel"),
  emptyOpenList: $("#btn-empty-open-list"),
};

const MOBILE_MQ = window.matchMedia("(max-width: 900px)");

function isMobileLayout() {
  return MOBILE_MQ.matches;
}

function setSidebarOpen(open) {
  if (!el.sidebar) return;
  el.sidebar.classList.toggle("open", open);
  el.sidebarBackdrop?.classList.toggle("open", open);
  el.sidebarBackdrop?.classList.toggle("hidden", !open);
  el.toggleSidebar?.setAttribute("aria-expanded", open ? "true" : "false");
  if (!isMobileLayout() && el.app) {
    el.app.classList.toggle("sidebar-collapsed", !open);
  }
}

function toggleSidebar() {
  if (isMobileLayout()) {
    const open = !el.sidebar?.classList.contains("open");
    setSidebarOpen(open);
  } else {
    const collapsed = el.app?.classList.contains("sidebar-collapsed");
    setSidebarOpen(collapsed); // if collapsed → open
  }
}

function closeHeaderMenu() {
  el.headerMenuPanel?.classList.add("hidden");
  el.headerMore?.setAttribute("aria-expanded", "false");
}

function toggleHeaderMenu() {
  const willOpen = el.headerMenuPanel?.classList.contains("hidden");
  if (willOpen) {
    el.headerMenuPanel?.classList.remove("hidden");
    el.headerMore?.setAttribute("aria-expanded", "true");
  } else {
    closeHeaderMenu();
  }
}

// ── API helpers ────────────────────────────────────────

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  // Не устанавливать Content-Type для FormData — браузер сам multipart/form-data с boundary
  const body = options.body;
  if (!(body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${API}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (res.status === 204) return null;

  // 401 — refresh по cookie, затем модалка входа
  if (res.status === 401) {
    if (state.authRetryCount > 0) {
      throw new Error(
        "Требуется авторизация для доступа к данным. Перезагрузите страницу или нажмите кнопку входа."
      );
    }

    state.authRetryCount++;

    if (jwtManager.isTokenValid() || jwtManager.userRole) {
      const refreshed = await jwtManager.refreshToken();
      if (refreshed) {
        state.authRetryCount = 0;
        const retryRes = await fetch(`${API}${path}`, {
          ...options,
          headers,
          credentials: "include",
        });
        if (retryRes.status === 204) return null;
        const retryText = await retryRes.text();
        let retryData = null;
        try {
          retryData = retryText ? JSON.parse(retryText) : null;
        } catch {
          retryData = { detail: retryText };
        }
        if (!retryRes.ok) {
          const msg =
            typeof retryData?.detail === "string"
              ? retryData.detail
              : Array.isArray(retryData?.detail)
                ? retryData.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
                : `Ошибка ${retryRes.status}`;
          throw new Error(msg);
        }
        state.authRetryCount = 0;
        return retryData;
      }
    }

    const success = await ensureJWToken();
    if (success) {
      state.authRetryCount = 0;
      state.isAuthenticated = true;
      return api(path, options);
    }
    throw new Error("Требуется авторизация для доступа к данным");
  }

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

  state.authRetryCount = 0;
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

// Обеспечение JWT токена через модалку
async function ensureJWToken() {
  if (!jwtManager.isTokenValid()) {
    if (state.authRetryCount > 0) {
      return false;
    }
    const success = await openAuthModal();
    if (!success) {
      return false;
    }
    return true;
  }
  return await jwtManager.refreshTokenIfNeeded();
}

// Проверка прав редактирования
async function requireEditorAccess() {
  if (!(await ensureJWToken())) {
    throw new Error("Требуется авторизация для доступа к данным");
  }

  if (jwtManager.userRole !== "editor") {
    const upgrade = confirm(
      "Текущий уровень доступа: просмотр.\nДля этой операции нужны права редактирования.\n\nВойти как редактор?"
    );
    if (upgrade) {
      state.authRetryCount = 0;
      const success = await openAuthModal({ preferEditor: true });
      if (success && jwtManager.userRole === "editor") {
        state.isAuthenticated = true;
        state.authRetryCount = 0;
        if (typeof window.__updateAuthUI === "function") window.__updateAuthUI();
        return true;
      }
      if (success && jwtManager.userRole !== "editor") {
        toast("Введённый токен не даёт прав редактирования", "error");
      }
    }
    throw new Error("Требуются права редактирования");
  }
  return true;
}

async function apiWithJWT(path, options = {}) {
  if (!(await ensureJWToken())) {
    throw new Error("Требуется авторизация для доступа к данным");
  }
  return api(path, options);
}

// API функция для редактирующих операций
async function apiWithEditorAccess(path, options = {}) {
  if (!(await requireEditorAccess())) {
    throw new Error("Требуются права редактирования");
  }
  return api(path, options);
}

// ── Fetch с cookie-сессией (FormData / произвольный URL) ──────────────────
async function fetchWithJWT(url, init = {}) {
  if (!(await ensureJWToken())) {
    throw new Error("Требуется авторизация для доступа к данным");
  }
  init = { ...init, credentials: "include" };
  const headers = new Headers(init.headers || {});
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

// Fetch с правами редактирования
async function fetchWithEditorAccess(url, init = {}) {
  if (!(await requireEditorAccess())) {
    throw new Error("Требуются права редактирования");
  }
  init = { ...init, credentials: "include" };
  const headers = new Headers(init.headers || {});
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
    return `<div class="${extraClass} ${g} has-photo"><img src="${escapeHtml(jwtManager.mediaUrl(person.photo_url))}" alt="" loading="lazy" /></div>`;
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
  let query = q ? `?q=${encodeURIComponent(q)}` : "";
  if (!q && state.familyFilter) query = `?family=${encodeURIComponent(state.familyFilter)}`;
  console.log(`loadPeople called with q: "${q}", query: "${query}"`);
  try {
    state.people = await api(`/persons${query}`);
    console.log(`Received ${state.people.length} people`);
    renderPersonList();
    fillRelationSelects();
  } catch (e) {
    console.error(`Error in loadPeople: ${e.message}`);
    if (e.message === "Authorization cancelled or failed") {
      // Пользователь отменил авторизацию - показываем пустой список
      state.people = [];
      renderPersonList();
    } else {
      toast(e.message || "Не удалось загрузить список людей", "error");
    }
  }
}

function welcomeHtml() {
  return `
    <div class="welcome-guide">
      <h3>🐴 Семейное дерево — с чего начать</h3>
      <ol>
        <li><b>Откройте свою ссылку доступа.</b> Её присылают лично (в сообщении
            или файле): одна — для просмотра, другая — для редактирования.</li>
        <li><b>Ничего вводить не нужно.</b> Открыв ссылку один раз, вы войдёте
            автоматически — сессия держится сутки и продлевается сама.</li>
        <li><b>Смотрите древо:</b> список людей слева, детали и фото — в панели
            справа, поиск — по фамилии или имени (можно по-русски).</li>
        <li><b>Если у вас ссылка редактирования</b> — можно добавлять людей,
            связи, фото и историю правок.</li>
      </ol>
      <p class="welcome-note">Потеряли ссылку? Попросите её снова у владельца —
      ссылки равносильны паролям и публично не публикуются.</p>
      <button type="button" class="btn btn-primary btn-sm" id="btn-list-login" style="margin-top:0.65rem">Войти по ссылке вручную</button>
    </div>`;
}

function renderPersonList() {
  if (!state.people.length) {
    if (state.isAuthenticated) {
      el.personList.innerHTML =
        '<p class="list-empty">Пока никого нет.<br>Добавьте первого человека.</p>';
    } else {
      el.personList.innerHTML = welcomeHtml();
      $("#btn-list-login")?.addEventListener("click", () => window.requestAuth());
    }
    return;
  }

  el.personList.innerHTML = state.people
    .map((p) => {
      const active = p.id === state.selectedId ? "active" : "";
      return `
        <div class="person-item ${active}" data-id="${p.id}" role="option" aria-selected="${p.id === state.selectedId}" tabindex="0">
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
  // На телефоне после выбора закрываем drawer, чтобы сразу видеть дерево
  if (isMobileLayout()) {
    setSidebarOpen(false);
  }
  try {
    await renderMainView();
    if (openDetail) await showDetail(id);
  } catch (e) {
    if (e.message === "Authorization cancelled or failed") {
      el.canvas.classList.add("hidden");
      el.empty.classList.remove("hidden");
    } else {
      toast(e.message, "error");
    }
  }
}

function renderPeopleOverview() {
  let grid = el.empty.querySelector(".people-overview");
  if (!grid) {
    grid = document.createElement("div");
    grid.className = "people-overview";
    el.empty.appendChild(grid);
  }
  if (!state.people.length) {
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = state.people
    .map(
      (p) => `
      <button type="button" class="overview-card" data-id="${p.id}">
        ${avatarHtml(p, "avatar")}
        <span class="overview-name">${escapeHtml(p.full_name)}</span>
        <span class="overview-sub">${escapeHtml(formatDates(p) || "")}</span>
      </button>`
    )
    .join("");
  grid.querySelectorAll(".overview-card").forEach((card) => {
    card.addEventListener("click", () => selectPerson(Number(card.dataset.id)));
  });
}

async function renderMainView() {
  if (!state.selectedId && state.view !== "path") {
    renderPeopleOverview();
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
    try {
      const tree = await api(
        `/tree/${state.selectedId}?depth_up=${state.depthUp}&depth_down=${state.depthDown}`
      );
      renderTree(tree);
      el.toolbarHint.textContent = `Узлов: ${tree.nodes_count}`;
    } catch (e) {
      if (e.message === "Authorization cancelled or failed") {
        el.canvas.classList.add("hidden");
        el.empty.classList.remove("hidden");
      } else {
        toast(e.message, "error");
      }
    }
  } else {
    try {
      const rel = await api(`/tree/${state.selectedId}/relatives`);
      renderRelativesView(rel);
      el.toolbarHint.textContent = "Ближайшие родственники";
    } catch (e) {
      if (e.message === "Authorization cancelled or failed") {
        el.canvas.classList.add("hidden");
        el.empty.classList.remove("hidden");
      } else {
        toast(e.message, "error");
      }
    }
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

// ── мобильная нижняя панель деталей: не накрывать дерево ──
function syncDetailPadding() {
  const sc = document.querySelector(".view-area");
  const dp = el.detail;
  if (!sc || !dp) return;
  if (window.innerWidth <= 900 && !dp.classList.contains("hidden")) {
    requestAnimationFrame(() => {
      const h = dp.classList.contains("collapsed") ? 34 : dp.getBoundingClientRect().height;
      sc.style.paddingBottom = h + 40 + "px";
    });
  } else {
    sc.style.paddingBottom = "";
  }
}

function hideDetailPanel() {
  el.detail.classList.add("hidden");
  document.body.classList.remove("detail-open");
  syncDetailPadding();
}

async function showDetail(id) {
  let person = state.people.find((p) => p.id === id);
  if (!person) {
    person = await api(`/persons/${id}`);
  }
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
    photoBlock.innerHTML = `<img src="${escapeHtml(jwtManager.mediaUrl(person.photo_url))}" alt="${escapeHtml(person.full_name)}" />`;
    photoBlock.classList.remove("hidden");
  } else {
    photoBlock.innerHTML = "";
    photoBlock.classList.add("hidden");
  }

  // ── все фото человека (галерея), кроме главного ───────────────
  loadPersonPhotos(id, photoBlock).catch(() => {});

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
  el.detail.classList.remove("collapsed");
  document.body.classList.add("detail-open");
  syncDetailPadding();

  // ── История правок ──────────────────────────────────────────
  loadPersonHistory(id).catch(() => {});
  renderPersonLinkButton(id);
}

const HISTORY_FIELD_LABELS = {
  first_name: "Имя",
  last_name: "Фамилия",
  middle_name: "Отчество",
  maiden_name: "Девичья фамилия",
  gender: "Пол",
  birth_date: "Дата рождения",
  death_date: "Дата смерти",
  birth_place: "Место рождения",
  notes: "Заметки",
  photo_path: "Фото",
};

const HISTORY_ACTION_LABELS = {
  create: "создан",
  update: "изменён",
  delete: "удалён",
};

function fmtHistoryValue(v) {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function fmtHistoryDate(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

async function loadPersonHistory(id) {
  let block = document.getElementById("detail-history");
  if (!block) {
    block = document.createElement("div");
    block.id = "detail-history";
    block.className = "detail-history";
    el.detailRel.insertAdjacentElement("afterend", block);
  }
  const items = await api(`/persons/${id}/history?limit=50`);
  if (!items.length) {
    block.innerHTML =
      '<h4>История правок</h4><p class="history-empty">Правок ещё не было</p>';
    return;
  }
  const rows = items
    .map((r) => {
      const changes = (r.changes || [])
        .map(
          (c) =>
            `<div class="history-change">${escapeHtml(
              HISTORY_FIELD_LABELS[c.field] || c.field
            )}: <s>${escapeHtml(fmtHistoryValue(c.old))}</s> → <b>${escapeHtml(
              fmtHistoryValue(c.new)
            )}</b></div>`
        )
        .join("");
      const who = r.actor ? `<span class="history-actor">✍ ${escapeHtml(r.actor)}</span>` : "";
      return `<div class="history-item">
        <div class="history-head">
          <span class="history-action history-action-${escapeHtml(r.action)}">${escapeHtml(
        HISTORY_ACTION_LABELS[r.action] || r.action
      )}</span>
          ${who}
          <time>${escapeHtml(fmtHistoryDate(r.created_at))}</time>
        </div>
        ${changes}
      </div>`;
    })
    .join("");
  block.innerHTML = `<h4>История правок</h4>${rows}`;
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
    photoImg.src = jwtManager.mediaUrl(person.photo_url);
    photoPreview.style.display = "block";
    photoPreview.classList.add("visible");
    placeholder.style.display = "none";
    delBtn.hidden = false;
    delBtn.style.display = "inline-flex";
    $("#p-photo-path").value = person.photo_url || "";
    photoSection.classList.remove("hidden");
  } else {
    photoPreview.style.display = "none";
    photoPreview.classList.remove("visible");
    placeholder.style.display = "block";
    delBtn.hidden = true;
    delBtn.style.display = "none";
    $("#p-photo-path").value = "";
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
      person = await apiWithEditorAccess(`/persons/${editId}`, {
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
      person = await apiWithEditorAccess("/persons", { method: "POST", body: JSON.stringify(body) });
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
    await apiWithEditorAccess("/relationships", { method: "POST", body: JSON.stringify(body) });
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
    hideDetailPanel();
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
    const data = await apiWithEditorAccess("/export/obsidian", { method: "POST" });
    toast(data.message || "Экспорт готов");
    if (data.warnings?.length) console.warn(data.warnings);

    const wantZip = confirm(
      `Готово: ${data.people} чел.\n\nПапка:\n${data.export_dir}\n\nСкачать ZIP?`
    );
    if (!wantZip) return;

    const res = await fetch(`${API}/export/obsidian/zip`, { credentials: "include" });
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
    const data = await fetchWithEditorAccess(`${API}/import/gramps?replace=${replace ? "true" : "false"}`, {
      method: "POST",
      body: form,
    });
    toast(data.message || "Импорт завершён");
    if (data.warnings?.length) {
      console.warn("Gramps import warnings:", data.warnings);
      toast(`Предупреждений: ${data.warnings.length} (см. консоль)`, "error");
    }
    state.selectedId = null;
    hideDetailPanel();
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
    await apiWithEditorAccess(`/persons/${id}`, { method: "DELETE" });
    toast("Человек удалён");
    hideDetailPanel();
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
    const media = await apiWithEditorAccess(`/persons/${editId}/photo?is_primary=true`, {
      method: "POST",
      body: form,
    });
    toast("Фото загружено");
    const photoImg = $("#photo-preview-img");
    const preview = $("#photo-preview");
    const delBtn = $("#btn-delete-photo");
    photoImg.src = media.url;
    preview.style.display = "block";
    preview.classList.add("visible");
    $("#photo-placeholder").style.display = "none";
    if (delBtn) {
      delBtn.hidden = false;
      delBtn.style.display = "inline-flex";
    }
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
    await apiWithEditorAccess(`/persons/${editId}/photo/all`, { method: "DELETE" });
    toast("Фото удалено");
    const preview = $("#photo-preview");
    const delBtn = $("#btn-delete-photo");
    preview.style.display = "none";
    preview.classList.remove("visible");
    $("#photo-placeholder").style.display = "block";
    if (delBtn) {
      delBtn.hidden = true;
      delBtn.style.display = "none";
    }
    $("#p-photo-path").value = "";
    $("#photo-preview-img").src = "";
  } catch (err) {
    toast(err.message || "Не удалось удалить фото", "error");
  }
}

// ── Relation type labels ─────────────────────────────

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
  el.search?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadPeople(el.search.value.trim()), 250);
  });

  el.personList?.addEventListener("click", (e) => {
    const item = e.target.closest(".person-item");
    if (item) selectPerson(Number(item.dataset.id), { openDetail: true });
  });

  el.personList?.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const item = e.target.closest(".person-item");
    if (item) {
      e.preventDefault();
      selectPerson(Number(item.dataset.id), { openDetail: true });
    }
  });

  el.canvas?.addEventListener("click", (e) => {
    const card = e.target.closest(".person-card");
    if (card) showDetail(Number(card.dataset.id));
  });

  el.detailRel?.addEventListener("click", (e) => {
    const chip = e.target.closest(".rel-chip");
    if (chip) selectPerson(Number(chip.dataset.id), { openDetail: true });
  });

  el.pathResult?.addEventListener("click", (e) => {
    if (e.target.id === "btn-back-tree") {
      state.view = "tree";
      $$("#view-tabs .tab").forEach((t) => {
        const on = t.dataset.view === "tree";
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      el.pathResult.classList.add("hidden");
      if (state.selectedId) renderMainView();
      else el.empty.classList.remove("hidden");
      return;
    }
    const chip = e.target.closest(".rel-chip");
    if (chip) selectPerson(Number(chip.dataset.id), { openDetail: true });
  });

  $("#btn-close-detail")?.addEventListener("click", () => el.detail.classList.add("hidden"));
  // тап по полоске-ручке сворачивает/разворачивает нижнюю панель на мобильном
  document.querySelector(".detail-sheet-handle")?.addEventListener("click", () => {
    if (window.innerWidth <= 900) {
      el.detail.classList.toggle("collapsed");
      syncDetailPadding();
    }
  });
  $("#btn-set-root")?.addEventListener("click", () => {
    const id = Number(el.detail.dataset.personId);
    if (id) {
      state.view = "tree";
      $$("#view-tabs .tab").forEach((t) => {
        const on = t.dataset.view === "tree";
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      selectPerson(id);
    }
  });
  $("#btn-edit-person")?.addEventListener("click", () => {
    const id = Number(el.detail.dataset.personId);
    if (id) openPersonModalEdit(id).catch((e) => toast(e.message, "error"));
  });
  $("#btn-delete-person")?.addEventListener("click", deleteSelectedPerson);

  $("#btn-add-person")?.addEventListener("click", openPersonModalCreate);
  $("#btn-upload-photo")?.addEventListener("click", () => $("#photo-file-input")?.click());
  $("#photo-file-input")?.addEventListener("change", onPhotoFileSelected);
  $("#btn-delete-photo")?.addEventListener("click", onDeletePhoto);
  $("#btn-add-relation")?.addEventListener("click", () => {
    fillRelationSelects();
    updateRelationLabels();
    openModal("modal-relation");
  });
  $("#btn-import")?.addEventListener("click", () => {
    closeHeaderMenu();
    $("#file-gramps")?.click();
  });
  $("#file-gramps")?.addEventListener("change", onImportGramps);
  $("#btn-export-obsidian")?.addEventListener("click", () => {
    closeHeaderMenu();
    onExportObsidian();
  });
  $("#btn-path")?.addEventListener("click", () => {
    closeHeaderMenu();
    fillRelationSelects();
    openModal("modal-path");
  });
  $("#btn-refresh")?.addEventListener("click", () => {
    if (state.selectedId) renderMainView().catch((e) => toast(e.message, "error"));
  });

  el.depthUp?.addEventListener("change", () => {
    if (state.selectedId && state.view === "tree") renderMainView();
  });
  el.depthDown?.addEventListener("change", () => {
    if (state.selectedId && state.view === "tree") renderMainView();
  });

  $$("#view-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      $$("#view-tabs .tab").forEach((t) => {
        const on = t === tab;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      el.pathResult.classList.add("hidden");
      if (state.selectedId) renderMainView().catch((e) => toast(e.message, "error"));
    });
  });

  $("#form-person")?.addEventListener("submit", onSavePerson);
  $("#form-relation")?.addEventListener("submit", onCreateRelation);
  $("#form-path")?.addEventListener("submit", onFindPath);
  $("#r-type")?.addEventListener("change", updateRelationLabels);
  $("#auth-level")?.addEventListener("change", updateAuthTokenHint);

  $$("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });

  $$(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.add("hidden");
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    // auth-модалка закрывается своим обработчиком (openAuthModal)
    if ($("#modal-auth")?.dataset.authPending === "1") return;
    const openModals = $$(".modal-overlay:not(.hidden)");
    if (openModals.length) {
      openModals.forEach((m) => m.classList.add("hidden"));
      return;
    }
    if (!el.headerMenuPanel?.classList.contains("hidden")) {
      closeHeaderMenu();
      return;
    }
    if (el.detail && !el.detail.classList.contains("hidden")) {
      hideDetailPanel();
      return;
    }
    if (isMobileLayout() && el.sidebar?.classList.contains("open")) {
      setSidebarOpen(false);
    }
  });

  // Sidebar drawer / collapse
  el.toggleSidebar?.addEventListener("click", () => toggleSidebar());
  el.closeSidebar?.addEventListener("click", () => setSidebarOpen(false));
  el.sidebarBackdrop?.addEventListener("click", () => setSidebarOpen(false));
  el.emptyOpenList?.addEventListener("click", () => setSidebarOpen(true));

  // Header overflow menu
  el.headerMore?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleHeaderMenu();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#header-menu")) closeHeaderMenu();
  });

  // Начальное состояние сайдбара
  if (isMobileLayout()) {
    setSidebarOpen(false);
    el.sidebarBackdrop?.classList.add("hidden");
  } else {
    setSidebarOpen(true);
  }

  MOBILE_MQ.addEventListener?.("change", () => {
    if (isMobileLayout()) {
      setSidebarOpen(false);
      el.app?.classList.remove("sidebar-collapsed");
    } else {
      el.sidebar?.classList.remove("open");
      el.sidebarBackdrop?.classList.remove("open");
      el.sidebarBackdrop?.classList.add("hidden");
      setSidebarOpen(true);
    }
  });

  // Auth UI
  const authStatus = document.getElementById("auth-status");
  const btnAuth = document.getElementById("btn-auth");

  function updateAuthUI() {
    const isAuthenticated = jwtManager.isTokenValid();
    const isEditor = jwtManager.userRole === "editor";
    state.isAuthenticated = isAuthenticated;
    state.userRole = jwtManager.userRole;

    if (authStatus) {
      if (isAuthenticated) {
        const roleText = isEditor ? "Редактор" : "Просмотр";
        authStatus.innerHTML = `<span class="auth-indicator auth-indicator--active" title="${roleText}"></span><span class="auth-role-text">${roleText}</span>`;
        authStatus.title = `Роль: ${roleText}`;
      } else {
        authStatus.innerHTML = `<span class="auth-indicator auth-indicator--inactive" title="Не авторизован"></span>`;
        authStatus.title = "Не авторизован";
      }
    }

    if (btnAuth) {
      btnAuth.textContent = isAuthenticated ? "🚪" : "🔐";
      btnAuth.title = isAuthenticated ? "Выйти" : "Войти";
      btnAuth.setAttribute("aria-label", isAuthenticated ? "Выйти" : "Войти");
    }

    const editButtons = [
      "btn-add-person",
      "btn-add-relation",
      "btn-import",
      "btn-export-obsidian",
      "btn-edit-person",
      "btn-delete-person",
      "btn-upload-photo",
      "btn-delete-photo",
      "btn-person-submit",
      "btn-back-tree",
    ].map((id) => document.getElementById(id));

    editButtons.forEach((btn) => {
      if (!btn) return;
      if (isAuthenticated && !isEditor) {
        btn.disabled = true;
        btn.title = "Требуются права редактирования";
        btn.classList.add("disabled");
      } else {
        btn.disabled = false;
        if (btn.title === "Требуются права редактирования") btn.title = "";
        btn.classList.remove("disabled");
      }
    });
  }

  window.__updateAuthUI = updateAuthUI;

  async function handleAuthClick() {
    state.authRetryCount = 0;
    const isAuthenticated = jwtManager.isTokenValid();

    if (isAuthenticated) {
      if (confirm("Выйти из системы?")) {
        jwtManager.clearSession(true);
        updateAuthUI();
        toast("Вы вышли из системы", "success");
        state.people = [];
        state.selectedId = null;
        el.canvas?.classList.add("hidden");
        el.detail?.classList.add("hidden");
        el.empty?.classList.remove("hidden");
        renderPersonList();
      }
      return;
    }

    const success = await openAuthModal();
    if (success) {
      updateAuthUI();
    }
  }

  btnAuth?.addEventListener("click", handleAuthClick);
  updateAuthUI();
  setInterval(updateAuthUI, 60000);
}

// ── Init ───────────────────────────────────────────────

/** Плашка активного отбора по фамилии + кнопка «показать всех». */
function showFamilyFilterChip(family) {
  let chip = document.getElementById("family-filter-chip");
  if (!chip) {
    chip = document.createElement("div");
    chip.id = "family-filter-chip";
    chip.className = "family-filter-chip";
    const bar = document.querySelector(".controls") || document.querySelector("main") || document.body;
    bar.insertAdjacentElement("afterbegin", chip);
  }
  chip.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = `👨‍👩‍👧 Семья: ${family} и близкие`;
  const btn = document.createElement("button");
  btn.className = "btn btn--ghost";
  btn.textContent = "Показать всех";
  btn.addEventListener("click", async () => {
    state.familyFilter = null;
    chip.remove();
    await loadPeople();
    renderMainView();
  });
  chip.append(label, btn);
}

/** Кнопка создания персональной ссылки доступа (только для редактора). */
async function renderPersonLinkButton(id) {
  if (!state.isAdmin) return; // ссылки создаёт только админ
  let btn = document.getElementById("detail-link-btn");
  if (btn) btn.remove();
  btn = document.createElement("button");
  btn.id = "detail-link-btn";
  btn.className = "btn btn--ghost";
  btn.textContent = "🔗 Ссылка для этого человека";
  btn.addEventListener("click", async () => {
    if (!confirm("Создать персональную ссылку доступа?\\nСсылка равносильна паролю — передавайте её только лично.")) return;
    const depthRaw = prompt(
      "Ограничить права редактирования семьёй этого человека?\\n" +
      "Введите глубину в поколениях (например, 2 — родители, дети, супруги и их родители/дети).\\n" +
      "Пусто или Отмена — без ограничений (всё дерево)."
    );
    const body = {};
    if (depthRaw && Number(depthRaw) > 0) body.scope_depth = Number(depthRaw);
    btn.disabled = true;
    try {
      const res = await fetch(`${API}/persons/${id}/access-link`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).detail || res.status);
      const data = await res.json();
      const url = location.origin + data.url;
      prompt(`Персональная ссылка (скопируйте и передайте лично):\\n${url}`, url);
      toast("Ссылка создана", "success");
    } catch (err) {
      toast(`Не удалось создать ссылку: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  });
  const hist = document.getElementById("detail-history");
  if (hist) hist.insertAdjacentElement("afterend", btn);
  else el.detailRel?.insertAdjacentElement("afterend", btn);
}

async function init() {
  bindEvents();

  // Восстановить сессию из httpOnly cookie
  await jwtManager.init();
  state.isAuthenticated = jwtManager.isTokenValid();
  state.userRole = jwtManager.userRole;
  state.isAdmin = !!jwtManager.isAdmin;
  if (typeof window.__updateAuthUI === "function") window.__updateAuthUI();

  if (jwtManager.isTokenValid()) {
    try {
      await loadPeople();
      // Человека автоматически не выбираем: при входе показываем обзор
      // всех людей с подсказкой, что нажать, чтобы увидеть древо.
      // Исключение — персональная ссылка: ?person=<id> из редиректа входа.
      const params = new URLSearchParams(location.search);
      const familyParam = params.get("family");
      if (familyParam) {
        history.replaceState(null, "", location.pathname + location.hash);
        state.familyFilter = familyParam;
        await loadPeople();
        state.selectedId = null;
        renderMainView();
        showFamilyFilterChip(familyParam);
        return;
      }
      const focusParam = params.get("person");
      const focusId = focusParam ? Number(focusParam) : null;
      if (focusId && state.people.some((p) => p.id === focusId)) {
        history.replaceState(null, "", location.pathname + location.hash);
        await selectPerson(focusId, { openDetail: true });
      } else {
        state.selectedId = null;
        renderMainView();
      }
    } catch (err) {
      console.error("Ошибка при загрузке данных:", err);
      toast(`Не удалось загрузить данные: ${err.message}`, "error");
    }
  } else {
    // Показываем сообщение о том, что нужна авторизация
    state.people = [];
    renderPersonList();
    toastJWT("Для доступа к семейному дереву требуется авторизация", "error");
  }
}

init();
async function loadPersonPhotos(personId, photoBlock) {
  const items = await api(`/persons/${personId}/photos`);
  const extra = items.filter((m) => !m.is_primary);
  let gallery = photoBlock.querySelector(".photo-gallery");
  if (!extra.length) {
    if (gallery) gallery.remove();
    return;
  }
  if (!gallery) {
    gallery = document.createElement("div");
    gallery.className = "photo-gallery";
    photoBlock.appendChild(gallery);
  }
  gallery.innerHTML = extra
    .map(
      (m) =>
        `<img class="photo-thumb" src="${escapeHtml(
          jwtManager.mediaUrl(m.url)
        )}" alt="${escapeHtml(m.description || "фото")}" loading="lazy" title="${escapeHtml(
          m.description || ""
        )}" />`
    )
    .join("");
}
