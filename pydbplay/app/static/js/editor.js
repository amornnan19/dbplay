// CodeMirror 6 SQL editor initialization
// Loaded as an ES module from /static/js/editor.js
//
// Per SPEC §7: mount on #sql-editor, dialect from data-dialect attribute,
// sync doc to hidden input (#sql-hidden) via htmx:configRequest listener,
// bind Mod-Enter to submit the parent form.
//
// Degrades gracefully: if the CDN import fails, the hidden textarea remains
// visible and functional as a plain text input.
//
// TODO(phase-autocomplete): Wire server-side autocomplete completion source.

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
 * Initialize a CodeMirror 6 SQL editor inside #sql-editor.
 *
 * Called from query.html as: window.initEditor(dialect, initialValue)
 *
 * @param {string} dialect      - one of "postgres" | "mysql" | "sqlite"
 * @param {string} initialValue - initial SQL content
 * @returns {EditorView | null}
 */
window.initEditor = function (dialect, initialValue) {
  const parent = document.getElementById("sql-editor");
  if (!parent) {
    console.warn("initEditor: element #sql-editor not found");
    return null;
  }

  const dialectDef = DIALECTS[dialect] ?? SQLite;

  const view = new EditorView({
    doc: initialValue || "",
    extensions: [
      basicSetup,
      sql({
        dialect: dialectDef,
        // TODO(phase-autocomplete): Replace with server-autocomplete source.
        schema: window.__schemaForAutocomplete ?? {},
      }),
      EditorView.theme({
        "&": { height: "200px" },
        ".cm-scroller": { overflow: "auto" },
      }),
      // Mod-Enter (⌘+Enter / Ctrl+Enter) → submit the parent form
      keymap.of([
        {
          key: "Mod-Enter",
          run: () => {
            const form = parent.closest("form");
            if (form) {
              // Sync hidden input before submit
              const hidden = document.getElementById("sql-hidden");
              if (hidden) {
                hidden.value = view.state.doc.toString();
              }
              form.requestSubmit();
            }
            return true;
          },
        },
      ]),
    ],
    parent,
  });

  // Expose __getValue so the htmx:configRequest listener can read the doc.
  parent.__getValue = () => view.state.doc.toString();

  // Expose __setValue so history clicks can load SQL back into the editor.
  parent.__setValue = (sql) => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: sql },
    });
  };

  return view;
};
