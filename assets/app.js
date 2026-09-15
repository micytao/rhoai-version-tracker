// Vanilla-JS client-side filtering for the Feature Lifecycle Matrix table.
// No build step, no framework -- operates directly on the server-rendered
// table's data-* attributes.
(function () {
  // Rows that share an "epic" (or, absent one, a category) are visually
  // merged via a shared background band (see .band-* in style.css) and a
  // single visible label in the leftmost Epic column -- server-rendered on
  // the group's first row. Filtering can hide that specific row while
  // leaving other rows of the same group visible, which would otherwise
  // orphan the group without a label. After every filter pass, re-run this
  // to move the visible label onto whichever row is now first-visible in
  // each group.
  function reattachEpicLabels(rows) {
    const firstVisibleByGroup = new Map();
    rows.forEach((row) => {
      row.classList.remove("show-epic-label");
      if (row.classList.contains("hidden-row")) return;
      const group = row.dataset.group || "";
      if (!firstVisibleByGroup.has(group)) {
        firstVisibleByGroup.set(group, row);
      }
    });
    firstVisibleByGroup.forEach((row) => row.classList.add("show-epic-label"));
  }

  function setupTableFilter({ tableId, searchId, categoryId, statusId }) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const rows = table.querySelectorAll("tbody tr");
    if (!rows.length) return;

    const searchInput = document.getElementById(searchId);
    const categorySelect = categoryId && document.getElementById(categoryId);
    const statusSelect = statusId && document.getElementById(statusId);

    function applyFilters() {
      const q = (searchInput && searchInput.value || "").trim().toLowerCase();
      const category = categorySelect && categorySelect.value || "";
      const status = statusSelect && statusSelect.value || "";

      rows.forEach((row) => {
        const name = (row.dataset.name || "").toLowerCase();
        const cat = row.dataset.category || "";
        const statuses = (row.dataset.statuses || "").split(",");
        const riskColors = (row.dataset.riskColors || "").split(",");

        const matchesSearch = !q || name.includes(q) || cat.toLowerCase().includes(q);
        const matchesCategory = !category || cat === category;
        const matchesStatus = !status || statuses.includes(status) || riskColors.includes(status);

        row.classList.toggle("hidden-row", !(matchesSearch && matchesCategory && matchesStatus));
      });

      reattachEpicLabels(rows);
    }

    [searchInput, categorySelect, statusSelect].forEach((el) => {
      if (el) el.addEventListener("input", applyFilters);
    });
  }

  // Versions before 3.0 are collapsed by default (see .legacy-col in
  // style.css); this just flips a class on the table wrapper and swaps the
  // button label/count.
  function setupLegacyToggle({ buttonId, wrapId }) {
    const btn = document.getElementById(buttonId);
    const wrap = document.getElementById(wrapId);
    if (!btn || !wrap) return;
    const count = btn.dataset.count || "";
    const showLabel = `Show ${count} version${count === "1" ? "" : "s"} before 3.0`;
    const hideLabel = `Hide version${count === "1" ? "" : "s"} before 3.0`;
    btn.textContent = showLabel;

    btn.addEventListener("click", () => {
      const expanded = wrap.classList.toggle("show-legacy");
      btn.textContent = expanded ? hideLabel : showLabel;
      btn.setAttribute("aria-expanded", String(expanded));
    });
  }

  setupTableFilter({
    tableId: "feature-matrix-table",
    searchId: "matrix-search",
    categoryId: "matrix-category",
    statusId: "matrix-status",
  });

  setupLegacyToggle({
    buttonId: "toggle-legacy-versions",
    wrapId: "matrix-wrap",
  });
})();
