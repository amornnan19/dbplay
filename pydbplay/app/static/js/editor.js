// CodeMirror 6 SQL editor initialization
// Loaded as an ES module from /static/js/editor.js
//
// Per SPEC §7: autocomplete source of truth = server endpoint
//   GET /api/c/{conn_id}/query/autocomplete?prefix=X
// Preload window.__schemaForAutocomplete only for small schemas.
//
// TODO(phase-1): Wire server-side autocomplete completion source.

import { EditorView, basicSetup } from "https://esm.sh/codemirror@6";
import { sql, PostgreSQL, MySQL, SQLite } from "https://esm.sh/@codemirror/lang-sql@6";
import { keymap } from "https://esm.sh/@codemirror/view@6";
import { defaultKeymap } from "https://esm.sh/@codemirror/commands@6";

const DIALECTS = {
  postgres: PostgreSQL,
  mysql: MySQL,
  sqlite: SQLite,
};

/**
 * Initialize a CodeMirror 6 SQL editor inside the given element.
 *
 * @param {string} elementId  - DOM element id to mount the editor into
 * @param {string} dialect    - one of "postgres" | "mysql" | "sqlite"
 * @param {string} initialValue - initial SQL content
 * @returns {EditorView}
 */
window.initEditor = function (elementId, dialect, initialValue) {
  const parent = document.getElementById(elementId);
  if (!parent) {
    console.warn(`initEditor: element #${elementId} not found`);
    return null;
  }

  const dialectDef = DIALECTS[dialect] ?? PostgreSQL;

  const view = new EditorView({
    doc: initialValue || "",
    extensions: [
      basicSetup,
      sql({
        dialect: dialectDef,
        // TODO(phase-1): Replace with server-autocomplete source; for small schemas
        //                only, pass window.__schemaForAutocomplete here.
        schema: window.__schemaForAutocomplete ?? {},
      }),
      EditorView.theme({
        "&": { height: "200px" },
        ".cm-scroller": { overflow: "auto" },
      }),
      // ⌘+Enter / Ctrl+Enter → submit the parent form
      keymap.of([
        {
          key: "Mod-Enter",
          run: () => {
            parent.closest("form")?.requestSubmit();
            return true;
          },
        },
      ]),
    ],
    parent,
  });

  // Expose getValue so the form submit handler can grab the current SQL
  parent.__getValue = () => view.state.doc.toString();

  return view;
};
