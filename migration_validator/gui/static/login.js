"use strict";

(function () {
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("login-error");
  const submit = form.querySelector("button[type=submit]");

  function showError(text) {
    errorEl.textContent = text;
    errorEl.hidden = false;
  }

  // The session cookie is Secure: over plain HTTP (except loopback) the browser
  // drops it and sign-in would silently loop back to this page.
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  if (window.location.protocol === "http:" && !loopback) {
    showError("Sign-in requires HTTPS. Open this page via https://.");
    submit.disabled = true;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.hidden = true;
    const username = form.username.value.trim();
    const password = form.password.value;
    if (!username || !password) {
      showError("Enter username and password.");
      return;
    }
    submit.disabled = true;
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        window.location.replace("/");
        return;
      }
      const body = await res.json().catch(() => ({}));
      showError(typeof body.detail === "string" ? body.detail : `Sign-in failed (${res.status}).`);
      form.password.value = "";
      form.password.focus();
    } catch (err) {
      showError("Server unreachable.");
    } finally {
      submit.disabled = false;
    }
  });
})();
