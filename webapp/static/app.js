const API = "/api";

const state = {
  page: 1,
  pageSize: 20,
  total: 0,
  filters: {
    newspaper: "",
    category: "",
    topic: "",
    date_from: "",
    date_to: "",
    q: "",
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
  clearFilters: document.getElementById("clearFilters"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageInfo: document.getElementById("pageInfo"),
  modalBackdrop: document.getElementById("modalBackdrop"),
  modal: document.getElementById("modal"),
  modalBody: document.getElementById("modalBody"),
  modalClose: document.getElementById("modalClose"),
};

let searchDebounce = null;

function qs(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== null && v !== undefined) usp.set(k, v);
  }
  return usp.toString();
}

async function fetchJSON(url) {
  const res = await fetch(url);
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

function renderCard(article) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.id = article.id;

  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.innerHTML = `
    <span class="newspaper">${escapeHtml(article.newspaper_name)}</span>
    <span>${formatDate(article.published_at)}</span>
    ${article.page_number ? `<span>p${article.page_number}</span>` : ""}
    ${article.byline ? `<span>${escapeHtml(article.byline)}</span>` : ""}
  `;

  const h3 = document.createElement("h3");
  h3.textContent = article.headline;

  const p = document.createElement("p");
  p.className = "summary";
  p.textContent = article.summary_text || "(no summary)";

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

  card.appendChild(meta);
  card.appendChild(h3);
  card.appendChild(p);
  card.appendChild(tagRow);
  card.addEventListener("click", () => openModal(article.id));
  return card;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
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

async function openModal(id) {
  els.modalBody.innerHTML = "<p>Loading...</p>";
  els.modalBackdrop.hidden = false;
  try {
    const a = await fetchJSON(`${API}/articles/${id}`);
    const bullets = (a.bullets || [])
      .map((b) => `<li>${escapeHtml(b)}</li>`)
      .join("");
    const entities = (a.entities || []).join(", ");
    const topics = (a.topics || [])
      .map(
        (t) =>
          `<span class="tag">${escapeHtml(t.topic)} ${(t.confidence * 100).toFixed(0)}%${
            t.rationale ? ` &mdash; ${escapeHtml(t.rationale)}` : ""
          }</span>`
      )
      .join(" ");

    els.modalBody.innerHTML = `
      <h2>${escapeHtml(a.headline)}</h2>
      <div class="modal-meta">
        <strong>${escapeHtml(a.newspaper_name)}</strong>
        &middot; ${formatDate(a.published_at)}
        ${a.page_number ? `&middot; page ${a.page_number}` : ""}
        ${a.byline ? `&middot; ${escapeHtml(a.byline)}` : ""}
        ${a.category ? `&middot; <span class="tag category">${escapeHtml(a.category)}</span>` : ""}
      </div>

      <section>
        <h4>Summary</h4>
        <p>${escapeHtml(a.summary_text || "(no summary)")}</p>
        ${bullets ? `<ul>${bullets}</ul>` : ""}
        ${a.why_it_matters ? `<p><em>Why it matters:</em> ${escapeHtml(a.why_it_matters)}</p>` : ""}
      </section>

      ${entities ? `<section><h4>Entities</h4><p>${escapeHtml(entities)}</p></section>` : ""}
      ${topics ? `<section><h4>Topics</h4><div class="tag-row">${topics}</div></section>` : ""}

      <section>
        <h4>Original article</h4>
        <button id="toggleOriginal" class="toggle-original">Show full text</button>
        <div class="original-text" id="originalText" hidden>${escapeHtml(a.original_text)}</div>
      </section>
    `;

    document.getElementById("toggleOriginal").addEventListener("click", (e) => {
      const box = document.getElementById("originalText");
      box.hidden = !box.hidden;
      e.target.textContent = box.hidden ? "Show full text" : "Hide full text";
    });
  } catch (err) {
    els.modalBody.innerHTML = `<p>Failed to load article: ${escapeHtml(err.message)}</p>`;
  }
}

function closeModal() {
  els.modalBackdrop.hidden = true;
  els.modalBody.innerHTML = "";
}

function applyFiltersFromInputs() {
  state.filters = {
    newspaper: els.newspaper.value,
    category: els.category.value,
    topic: els.topic.value,
    date_from: els.dateFrom.value,
    date_to: els.dateTo.value,
    q: els.search.value.trim(),
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

els.clearFilters.addEventListener("click", () => {
  els.search.value = "";
  els.newspaper.value = "";
  els.category.value = "";
  els.topic.value = "";
  els.dateFrom.value = "";
  els.dateTo.value = "";
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

els.modalClose.addEventListener("click", closeModal);
els.modalBackdrop.addEventListener("click", (e) => {
  if (e.target === els.modalBackdrop) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
  if (e.key === "/" && document.activeElement !== els.search) {
    e.preventDefault();
    els.search.focus();
  }
});

loadFilterOptions();
loadArticles();
