// Custom HTMX extensions for pydbplay

// CSRF double-submit cookie injection.
// The server sets a JS-readable `csrf_token` cookie (HttpOnly=false, SameSite=Strict).
// For every HTMX request we read that cookie and send it back in the X-CSRFToken header.
// This satisfies the middleware's CSRF check without requiring any server-side session.
(function () {
  "use strict";

  /**
   * Read a cookie by name. Returns an empty string when the cookie is absent.
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

  document.body.addEventListener("htmx:configRequest", function (event) {
    const token = getCookie("csrf_token");
    if (token) {
      event.detail.headers["X-CSRFToken"] = token;
    }
  });
})();
