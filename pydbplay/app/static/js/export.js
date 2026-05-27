// Export download helper for pydbplay.
//
// Uses a transient hidden <form> POST so the browser streams the response
// body directly to a file (Content-Disposition: attachment).  A plain HTMX
// swap or fetch() + blob would buffer the entire export in memory.
//
// CSRF is sent via a hidden form field (``csrf_token``) — the security
// middleware accepts either the X-CSRFToken header OR this field for
// application/x-www-form-urlencoded bodies.  A plain-form POST cannot set
// custom headers, so the form-field path is the only viable option here.

(function () {
  "use strict";

  /**
   * Read a cookie by name. Returns an empty string when the cookie is absent.
   * (Same implementation as htmx-ext.js — kept in sync to avoid a shared dep.)
   * @param {string} name
   * @returns {string}
   */
  function getCookie(name) {
    const prefix = name + "=";
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const c = cookies[i].trim();
      if (c.startsWith(prefix)) {
        return decodeURIComponent(c.slice(prefix.length));
      }
    }
    return "";
  }

  /**
   * Trigger a file download by submitting a transient hidden form to the
   * export endpoint.  The browser handles streaming the attachment to disk.
   *
   * @param {string|number} connId  — connection profile id
   * @param {Object} params
   * @param {string} params.format  — "csv" | "json" | "sql"
   * @param {string} [params.sql]   — SQL query to export (mutually exclusive with table)
   * @param {string} [params.table] — table name to export (mutually exclusive with sql)
   */
  window.__exportDownload = function (connId, params) {
    const format = params.format || "csv";
    const action = "/api/c/" + connId + "/export?format=" + encodeURIComponent(format);

    const form = document.createElement("form");
    form.method = "POST";
    form.action = action;
    // application/x-www-form-urlencoded — required for the CSRF form-field path
    form.enctype = "application/x-www-form-urlencoded";
    // target="_blank" would open a new tab; omit to reuse the current tab's
    // network channel — the browser still saves the attachment to disk without
    // navigating away because of the Content-Disposition: attachment header.

    function addHidden(name, value) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.appendChild(input);
    }

    // Data field — exactly one of sql or table
    if (params.sql != null) {
      addHidden("sql", params.sql);
    } else if (params.table != null) {
      addHidden("table", params.table);
    }

    // CSRF double-submit cookie → form field
    const csrfToken = getCookie("csrf_token");
    if (csrfToken) {
      addHidden("csrf_token", csrfToken);
    }

    document.body.appendChild(form);
    form.submit();
    // Small delay before removal so the browser has time to initiate the
    // network request before the form element disappears from the DOM.
    setTimeout(function () {
      document.body.removeChild(form);
    }, 200);
  };
})();
