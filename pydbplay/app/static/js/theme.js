// theme.js — Phase 5: 3-state dark mode toggle (light / dark / system)
//
// Registers Alpine.store('theme', ...) on alpine:init.
// State is persisted at localStorage key 'pydbplay:theme'.
// _apply() toggles the 'dark' class on <html> based on effective preference.
// Also listens to the OS prefers-color-scheme media query so that 'system'
// mode reacts to the user switching their OS theme without a page reload.

const LS_THEME_KEY = "pydbplay:theme";

const _mq = window.matchMedia("(prefers-color-scheme: dark)");

function _prefersDark() {
  return _mq.matches;
}

function _loadTheme() {
  try {
    const stored = localStorage.getItem(LS_THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      return stored;
    }
  } catch (_) {
    // localStorage unavailable — degrade silently
  }
  return "system";
}

function _saveTheme(value) {
  try {
    localStorage.setItem(LS_THEME_KEY, value);
  } catch (_) {}
}

function _applyTheme(value) {
  const effectiveDark =
    value === "dark" || (value === "system" && _prefersDark());
  if (effectiveDark) {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}

document.addEventListener("alpine:init", () => {
  Alpine.store("theme", {
    value: _loadTheme(),

    init() {
      _applyTheme(this.value);
    },

    cycle() {
      const order = ["light", "dark", "system"];
      const idx = order.indexOf(this.value);
      this.value = order[(idx + 1) % order.length];
      this._apply();
      _saveTheme(this.value);
    },

    set(v) {
      if (v === "light" || v === "dark" || v === "system") {
        this.value = v;
        this._apply();
        _saveTheme(v);
      }
    },

    _apply() {
      _applyTheme(this.value);
    },
  });

  // Run init after store is registered
  Alpine.store("theme").init();
});

// Reapply when OS preference changes (relevant only in 'system' mode)
_mq.addEventListener("change", () => {
  const store = Alpine.store("theme");
  if (store && store.value === "system") {
    store._apply();
  }
});
