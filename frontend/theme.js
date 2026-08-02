/*
 * Light/dark theme switch.
 *
 * The chosen theme lives on <html data-theme>, which theme.css keys off. An
 * explicit choice is stored and always beats the OS preference; visitors who
 * have never chosen follow prefers-color-scheme and keep following it if they
 * change it mid-session.
 *
 * The attribute is set by a small inline script in each page's <head> so the
 * first paint is already correct. This file only wires up the control.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "course-search-theme";
  var root = document.documentElement;

  function storedTheme() {
    try {
      var value = localStorage.getItem(STORAGE_KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch (error) {
      return null; // private mode, or storage disabled
    }
  }

  function systemTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function currentTheme() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  // Screenshots are theme-specific, so swap any that offer both variants.
  function syncThemedImages(theme) {
    var images = document.querySelectorAll("img[data-light][data-dark]");
    for (var i = 0; i < images.length; i += 1) {
      var next = images[i].getAttribute(theme === "dark" ? "data-dark" : "data-light");
      if (next && images[i].getAttribute("src") !== next) {
        images[i].setAttribute("src", next);
      }
    }
  }

  function syncControls(theme) {
    var isDark = theme === "dark";
    var toggles = document.querySelectorAll(".theme-toggle");
    for (var i = 0; i < toggles.length; i += 1) {
      toggles[i].setAttribute("aria-checked", isDark ? "true" : "false");
    }
    var switches = document.querySelectorAll(".theme-switch");
    for (var j = 0; j < switches.length; j += 1) {
      switches[j].setAttribute("data-active", theme);
    }
  }

  function applyTheme(theme, persist) {
    root.setAttribute("data-theme", theme);
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, theme);
      } catch (error) {
        /* storage unavailable; the theme still applies for this page */
      }
    }
    syncControls(theme);
    syncThemedImages(theme);
  }

  function init() {
    // The inline head script already set the attribute; mirror it into the UI.
    applyTheme(currentTheme(), false);

    document.addEventListener("click", function (event) {
      var toggle = event.target.closest && event.target.closest(".theme-toggle");
      if (!toggle) {
        return;
      }
      applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
    });

    // Follow the OS only while the visitor has not made a choice of their own.
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        if (!storedTheme()) {
          applyTheme(systemTheme(), false);
        }
      };
      if (query.addEventListener) {
        query.addEventListener("change", onChange);
      } else if (query.addListener) {
        query.addListener(onChange);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
