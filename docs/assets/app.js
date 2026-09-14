// Vanilla-JS client-side filtering for the Feature Lifecycle Matrix.
// No build step, no framework -- operates directly on the server-rendered
// table's data-* attributes.
(function () {
  const searchInput = document.getElementById("matrix-search");
  const categorySelect = document.getElementById("matrix-category");
  const statusSelect = document.getElementById("matrix-status");
  const rows = document.querySelectorAll("table.matrix tbody tr");

  if (!rows.length) return;

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
  }

  [searchInput, categorySelect, statusSelect].forEach((el) => {
    if (el) el.addEventListener("input", applyFilters);
  });
})();
