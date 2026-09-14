// Vanilla-JS client-side filtering for the Feature Lifecycle Matrix table.
// No build step, no framework -- operates directly on the server-rendered
// table's data-* attributes.
(function () {
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
    }

    [searchInput, categorySelect, statusSelect].forEach((el) => {
      if (el) el.addEventListener("input", applyFilters);
    });
  }

  setupTableFilter({
    tableId: "feature-matrix-table",
    searchId: "matrix-search",
    categoryId: "matrix-category",
    statusId: "matrix-status",
  });
})();
