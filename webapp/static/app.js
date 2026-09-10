const API = "/api";

const state = {
  page: 1,
  pageSize: 10,
  total: 0,
  filters: {
    newspaper: "",
    category: "",
    topic: "",
    date_from: "",
    date_to: "",
    q: "",
    unread_only: false,
  },
};

const els = {
  list: document.getElementById("list"),
  stats: document.getElementById("stats"),
  search: document.getElementById("search"),
  newspaper: document.getElementById("newspaper"),
  category: document.getElementById("category"),
  topic: document.getElementById("topic"),
  dateFrom: document.getElementById("dateFrom"),
  dateTo: document.getElementById("dateTo"),
  unreadOnly: document.getElementById("unreadOnly"),
  clearFilters: document.getElementById("clearFilters"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageInfo: document.getElementById("pageInfo"),
};

let searchDebounce = null;

function qs(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== null && v !== undefined && v !== false) usp.set(k, v);
  }
  return usp.toString();
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function loadFilterOptions() {
  try {
    const [newspapers, categories, topics] = await Promise.all([
      fetchJSON(`${API}/newspapers`),
      fetchJSON(`${API}/categories`),
      fetchJSON(`${API}/topics`),
    ]);

    for (const n of newspapers) {
      const opt = document.createElement("option");
      opt.value = n.name;
      opt.textContent = `${n.name} (${n.article_count})`;
      els.newspaper.appendChild(opt);
    }
    for (const c of categories) {
      const opt = document.createElement("option");
      opt.value = c.category;
      opt.textContent = `${c.category} (${c.count})`;
      els.category.appendChild(opt);
    }
    for (const t of topics) {
      const opt = document.createElement("option");
      opt.value = t.name;
      opt.textContent = `${t.name} (${t.article_count})`;
      els.topic.appendChild(opt);
    }

    const totalArticles = newspapers.reduce((sum, n) => sum + n.article_count, 0);
    els.stats.textContent = `${totalArticles} articles across ${newspapers.length} edition(s)`;
  } catch (err) {
    console.error("Failed to load filter options", err);
  }
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function renderCard(article) {
  const card = document.createElement("article");
  card.className = "card" + (article.is_read ? " is-read" : "");
  card.dataset.id = article.id;

  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.innerHTML = `
    <span class="newspaper">${escapeHtml(article.newspaper_name)}</span>
    <span>${formatDate(article.published_at)}</span>
    ${article.page_number ? `<span>p${article.page_number}</span>` : ""}
    ${article.byline ? `<span>${escapeHtml(article.byline)}</span>` : ""}
    ${article.is_read ? '<span class="read-badge">Read</span>' : ""}
  `;
  const markBtn = document.createElement("button");
  markBtn.className = "mark-read-btn";
  markBtn.textContent = article.is_read ? "Mark unread" : "Mark read";
  markBtn.addEventListener("click", () => toggleRead(article.id, !article.is_read));
  meta.appendChild(markBtn);

  const h3 = document.createElement("h3");
  h3.textContent = article.headline;

  const p = document.createElement("p");
  p.className = "summary";
  p.textContent = article.summary_text || "(no summary)";

  const bullets = document.createElement("ul");
  bullets.className = "bullets";
  for (const b of article.bullets || []) {
    const li = document.createElement("li");
    li.textContent = b;
    bullets.appendChild(li);
  }

  let why = null;
  if (article.why_it_matters) {
    why = document.createElement("p");
    why.className = "why-matters";
    why.innerHTML = `<em>Why it matters:</em> ${escapeHtml(article.why_it_matters)}`;
  }

  let entities = null;
  if (article.entities && article.entities.length) {
    entities = document.createElement("p");
    entities.className = "entities";
    entities.innerHTML = `<strong>Entities:</strong> ${escapeHtml(article.entities.join(", "))}`;
  }

  const tagRow = document.createElement("div");
  tagRow.className = "tag-row";
  if (article.category) {
    const catTag = document.createElement("span");
    catTag.className = "tag category";
    catTag.textContent = article.category;
    tagRow.appendChild(catTag);
  }
  for (const t of article.topics || []) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = `${t.topic} ${(t.confidence * 100).toFixed(0)}%`;
    tagRow.appendChild(tag);
  }

  const toggleBtn = document.createElement("button");
  toggleBtn.className = "toggle-original secondary";
  toggleBtn.textContent = "Show original article";
  const originalBox = document.createElement("div");
  originalBox.className = "original-text";
  originalBox.hidden = true;
  toggleBtn.addEventListener("click", () => toggleOriginal(article.id, toggleBtn, originalBox));

  card.appendChild(meta);
  card.appendChild(h3);
  card.appendChild(p);
  if (bullets.childElementCount) card.appendChild(bullets);
  if (why) card.appendChild(why);
  if (entities) card.appendChild(entities);
  card.appendChild(tagRow);
  card.appendChild(toggleBtn);
  card.appendChild(originalBox);
  return card;
}

async function toggleOriginal(id, button, box) {
  if (!box.hidden) {
    box.hidden = true;
    button.textContent = "Show original article";
    return;
  }
  if (!box.dataset.loaded) {
    box.hidden = false;
    box.classList.add("loading");
    box.textContent = "Loading original text...";
    try {
      const article = await fetchJSON(`${API}/articles/${id}`);
      box.textContent = article.original_text || "(no original text stored)";
      box.dataset.loaded = "1";
    } catch (err) {
      box.textContent = `Failed to load original text: ${err.message}`;
    }
    box.classList.remove("loading");
  } else {
    box.hidden = false;
  }
  button.textContent = "Hide original article";
}

async function toggleRead(id, read) {
  try {
    await fetchJSON(`${API}/articles/${id}/read`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ read }),
    });
    if (state.filters.unread_only && read) {
      // The card no longer belongs in an unread-only view -- drop it and
      // reload so pagination counts stay correct.
      loadArticles();
    } else {
      const card = els.list.querySelector(`[data-id="${id}"]`);
      if (card) {
        card.classList.toggle("is-read", read);
        const btn = card.querySelector(".mark-read-btn");
        if (btn) btn.textContent = read ? "Mark unread" : "Mark read";
        const meta = card.querySelector(".card-meta");
        let badge = meta.querySelector(".read-badge");
        if (read && !badge) {
          badge = document.createElement("span");
          badge.className = "read-badge";
          badge.textContent = "Read";
          meta.insertBefore(badge, meta.querySelector(".mark-read-btn"));
        } else if (!read && badge) {
          badge.remove();
        }
      }
    }
  } catch (err) {
    console.error("Failed to update read status", err);
    alert(`Could not update read status: ${err.message}`);
  }
}

async function loadArticles() {
  els.list.innerHTML = '<p class="empty">Loading...</p>';
  const params = qs({
    ...state.filters,
    page: state.page,
    page_size: state.pageSize,
  });
  try {
    const data = await fetchJSON(`${API}/articles?${params}`);
    state.total = data.total;
    els.list.innerHTML = "";
    if (!data.items.length) {
      els.list.innerHTML = '<p class="empty">No articles match these filters.</p>';
    } else {
      for (const article of data.items) {
        els.list.appendChild(renderCard(article));
      }
    }
    updatePagination();
  } catch (err) {
    els.list.innerHTML = `<p class="empty">Failed to load articles: ${escapeHtml(err.message)}</p>`;
  }
}

function updatePagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  els.pageInfo.textContent = `Page ${state.page} of ${totalPages} (${state.total} total)`;
  els.prevPage.disabled = state.page <= 1;
  els.nextPage.disabled = state.page >= totalPages;
}

function applyFiltersFromInputs() {
  state.filters = {
    newspaper: els.newspaper.value,
    category: els.category.value,
    topic: els.topic.value,
    date_from: els.dateFrom.value,
    date_to: els.dateTo.value,
    q: els.search.value.trim(),
    unread_only: els.unreadOnly.checked,
  };
  state.page = 1;
  loadArticles();
}

els.search.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(applyFiltersFromInputs, 300);
});
els.newspaper.addEventListener("change", applyFiltersFromInputs);
els.category.addEventListener("change", applyFiltersFromInputs);
els.topic.addEventListener("change", applyFiltersFromInputs);
els.dateFrom.addEventListener("change", applyFiltersFromInputs);
els.dateTo.addEventListener("change", applyFiltersFromInputs);
els.unreadOnly.addEventListener("change", applyFiltersFromInputs);

els.clearFilters.addEventListener("click", () => {
  els.search.value = "";
  els.newspaper.value = "";
  els.category.value = "";
  els.topic.value = "";
  els.dateFrom.value = "";
  els.dateTo.value = "";
  els.unreadOnly.checked = false;
  applyFiltersFromInputs();
});

els.prevPage.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadArticles();
  }
});
els.nextPage.addEventListener("click", () => {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (state.page < totalPages) {
    state.page += 1;
    loadArticles();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== els.search) {
    e.preventDefault();
    els.search.focus();
  }
});

loadFilterOptions();
loadArticles();
