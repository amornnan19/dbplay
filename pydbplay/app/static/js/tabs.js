// tabs.js — Phase 4: multi-connection tab bar
//
// Registers two Alpine stores on alpine:init:
//   Alpine.store('profiles', ...) — profile metadata embedded by base.html
//   Alpine.store('tabs', ...)     — tab order + active, persisted in localStorage
//
// Tab state = { order: [int, ...], active: int | null }
// Stored at localStorage key 'pydbplay:tabs'.
//
// Per-tab SQL is persisted at localStorage['pydbplay:tab:<id>:sql'].
// Per-tab scroll is persisted at sessionStorage['pydbplay:tab:<id>:scroll'].

const LS_TABS_KEY = "pydbplay:tabs";
const SQL_MAX_BYTES = 256000;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _persist(state) {
  try {
    localStorage.setItem(LS_TABS_KEY, JSON.stringify({ order: state.order, active: state.active }));
  } catch (e) {
    // QuotaExceededError — degrade silently
  }
}

function _load() {
  try {
    const raw = localStorage.getItem(LS_TABS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        order: Array.isArray(parsed.order) ? parsed.order.map(Number) : [],
        active: parsed.active != null ? Number(parsed.active) : null,
      };
    }
  } catch (_) {
    // Corrupt data — start fresh
  }
  return { order: [], active: null };
}

// ---------------------------------------------------------------------------
// Alpine stores — registered on alpine:init
// ---------------------------------------------------------------------------

document.addEventListener("alpine:init", () => {
  // profiles store — read-only metadata from server
  Alpine.store("profiles", {
    all: window.__pydbplayProfiles || [],
    get byId() {
      return Object.fromEntries(this.all.map((p) => [p.id, p]));
    },
  });

  // tabs store
  Alpine.store("tabs", {
    order: [],
    active: null,

    init() {
      const saved = _load();
      this.order = saved.order;
      this.active = saved.active;

      const validIds = new Set((window.__pydbplayProfiles || []).map((p) => p.id));
      this.pruneStale(validIds);
      this.hydrateFromUrl(window.__pydbplayActiveConnId);
    },

    open(id) {
      const numId = Number(id);
      if (!this.order.includes(numId)) {
        this.order.push(numId);
      }
      this.active = numId;
      _persist(this);
    },

    close(id) {
      const numId = Number(id);
      const idx = this.order.indexOf(numId);
      if (idx === -1) return;

      this.order.splice(idx, 1);

      // Clean up per-tab storage for the closed tab
      try { localStorage.removeItem("pydbplay:tab:" + numId + ":sql"); } catch (_) {}
      try { sessionStorage.removeItem("pydbplay:tab:" + numId + ":scroll"); } catch (_) {}

      if (this.active === numId) {
        // Activate the tab to the right, or left if none
        const nextId = this.order[idx] ?? this.order[idx - 1] ?? null;
        this.active = nextId;
        _persist(this);
        if (nextId != null) {
          window.location = "/c/" + nextId;
        } else {
          window.location = "/connections";
        }
      } else {
        _persist(this);
      }
    },

    activate(id) {
      this.active = Number(id);
      _persist(this);
    },

    nth(i) {
      return this.order[i - 1] ?? null;
    },

    hydrateFromUrl(id) {
      if (id == null) return;
      const numId = Number(id);
      if (!this.order.includes(numId)) {
        this.order.push(numId);
      }
      this.active = numId;
      _persist(this);
    },

    pruneStale(validIds) {
      const before = this.order.length;
      const pruned = this.order.filter((id) => !validIds.has(id));
      this.order = this.order.filter((id) => validIds.has(id));

      // Clean up per-tab storage for every pruned tab
      for (const id of pruned) {
        try { localStorage.removeItem("pydbplay:tab:" + id + ":sql"); } catch (_) {}
        try { sessionStorage.removeItem("pydbplay:tab:" + id + ":scroll"); } catch (_) {}
      }

      let activeChanged = false;
      if (this.active != null && !validIds.has(this.active)) {
        this.active = this.order[0] ?? null;
        activeChanged = true;
      }
      if (this.order.length !== before || activeChanged) {
        _persist(this);
      }
    },
  });

  // Run init after both stores are registered
  Alpine.store("tabs").init();
});

// ---------------------------------------------------------------------------
// Keyboard shortcuts — Cmd/Ctrl + 1..9
// ---------------------------------------------------------------------------

window.addEventListener("keydown", (e) => {
  if (e.target && e.target.closest(".cm-editor, input, textarea, select")) return;

  if ((e.metaKey || e.ctrlKey) && e.key >= "1" && e.key <= "9") {
    const tabsStore = Alpine.store("tabs");
    if (!tabsStore) return;
    const id = tabsStore.nth(Number(e.key));
    if (id != null) {
      e.preventDefault();
      window.location = "/c/" + id;
    }
  }
});

// ---------------------------------------------------------------------------
// Scroll preservation
// ---------------------------------------------------------------------------

(function () {
  const connId = window.__pydbplayActiveConnId;

  // Restore scroll on load
  const restoreScroll = () => {
    if (connId == null) return;
    const key = "pydbplay:tab:" + connId + ":scroll";
    try {
      const saved = sessionStorage.getItem(key);
      if (saved != null) {
        window.scrollTo(0, Number(saved));
      }
    } catch (_) {}
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restoreScroll);
  } else {
    restoreScroll();
  }

  // Save scroll on scroll (debounced)
  if (connId != null) {
    let _scrollTimer = null;
    window.addEventListener("scroll", () => {
      clearTimeout(_scrollTimer);
      _scrollTimer = setTimeout(() => {
        const key = "pydbplay:tab:" + connId + ":scroll";
        try {
          sessionStorage.setItem(key, String(window.scrollY));
        } catch (_) {}
      }, 150);
    });
  }
})();
