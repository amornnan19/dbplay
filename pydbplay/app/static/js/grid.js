/**
 * grid.js — inline cell edit for the row browser (Phase 2).
 *
 * Double-click a cell → replace display span with an <input>.
 * Enter   → submit PATCH via HTMX, swap the returned <tr>.
 * Escape  → cancel, restore display span.
 *
 * HTMX-driven: the actual request is sent by htmx.ajax() so the CSRF hook
 * in htmx-ext.js still fires.  Cell values are read from the <input> DOM
 * element — they are NEVER interpolated into JS strings or event handlers.
 *
 * TODO(phase-polish): optimistic update + rollback
 */

(function () {
  "use strict";

  /**
   * Build the pk_<col>=<val> params object from data-pk-* attributes on *td*.
   * @param {HTMLElement} td
   * @returns {Object}
   */
  function getPkParams(td) {
    const params = {};
    for (const attr of td.attributes) {
      if (attr.name.startsWith("data-pk-")) {
        const col = attr.name.slice("data-pk-".length);
        params["pk_" + col] = attr.value;
      }
    }
    return params;
  }

  /**
   * Enter edit mode for *td*.
   * Replaces the .cell-display span with a text input.
   * @param {HTMLElement} td
   */
  window.__startEdit = function (td) {
    // Don't double-enter edit mode
    if (td.querySelector("input.cell-edit")) return;

    const col = td.dataset.col;
    const currentValue = td.dataset.value;

    const display = td.querySelector(".cell-display");
    if (display) display.style.display = "none";

    const input = document.createElement("input");
    input.type = "text";
    input.value = currentValue;
    input.className =
      "cell-edit border border-indigo-400 rounded px-1 py-0 text-xs font-mono w-full focus:outline-none bg-white";

    td.appendChild(input);
    input.focus();
    input.select();

    // Cancel on Escape
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        _cancelEdit(td, display, input);
      } else if (e.key === "Enter") {
        e.preventDefault();
        _submitEdit(td, col, input, display);
      }
    });

    // Submit on blur (click away)
    input.addEventListener("blur", function () {
      // Short delay to let Enter key handler fire first
      setTimeout(function () {
        if (td.querySelector("input.cell-edit")) {
          _submitEdit(td, col, input, display);
        }
      }, 150);
    });
  };

  function _cancelEdit(td, display, input) {
    input.remove();
    if (display) display.style.display = "";
  }

  function _submitEdit(td, col, input, display) {
    // Guard: already submitted
    if (!td.querySelector("input.cell-edit")) return;

    const newValue = input.value;
    const row = td.closest("tr");
    const pkParams = getPkParams(td);
    const connId = td.closest("[data-conn-id]")
      ? td.closest("[data-conn-id]").dataset.connId
      : _inferConnId();
    const tableName = td.closest("[data-table]")
      ? td.closest("[data-table]").dataset.table
      : _inferTable();

    if (!connId || !tableName) {
      // Can't determine target — cancel
      _cancelEdit(td, display, input);
      return;
    }

    // Remove input, show loading state
    input.remove();
    if (display) {
      display.style.display = "";
      display.textContent = "…";
    }

    const formData = new FormData();
    formData.append("column", col);
    formData.append("value", newValue);
    for (const [k, v] of Object.entries(pkParams)) {
      formData.append(k, v);
    }

    htmx.ajax("PATCH", "/api/c/" + connId + "/rows/" + tableName, {
      source: row,
      target: row,
      swap: "outerHTML",
      values: formData,
    });
  }

  /**
   * Fallback: infer conn_id from the page URL (/c/<id>/browse/<table>).
   */
  function _inferConnId() {
    const m = location.pathname.match(/\/c\/(\d+)\//);
    return m ? m[1] : null;
  }

  /**
   * Fallback: infer table name from the page URL.
   */
  function _inferTable() {
    const m = location.pathname.match(/\/browse\/([^/]+)/);
    return m ? m[1] : null;
  }
})();
