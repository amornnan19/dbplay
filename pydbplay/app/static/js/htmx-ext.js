// Custom HTMX extensions for pydbplay
//
// TODO(phase-1): Add CSRF token injection on mutating requests (POST/PATCH/DELETE).
//                See SPEC §9 Security — Host/Origin validation + CSRF.
//
// TODO(phase-1): Add error-response handler that displays inline error messages
//                from the server within the HTMX swap target.

// Example extension scaffold — uncomment and flesh out as needed:
//
// htmx.defineExtension("pydbplay", {
//   onEvent(name, event) {
//     if (name === "htmx:configRequest") {
//       event.detail.headers["X-CSRF-Token"] = window.__csrfToken;
//     }
//   },
// });
