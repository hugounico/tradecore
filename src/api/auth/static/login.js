// TradeCore · login.js (Design §5). Navegador <-> Supabase; solo el access_token llega a
// TradeCore (POST same-origin a /auth/enter). No guarda nada en el navegador ni escribe en
// la consola. El token de renovacion de Supabase nunca se lee.
(function () {
  "use strict";

  var MSG_BAD_CREDENTIALS = "Credenciales inválidas";
  var MSG_UNAVAILABLE = "Servicio de acceso no disponible, intenta más tarde";
  var MSG_LOGIN_FAILED = "No fue posible ingresar. Intenta nuevamente.";
  var MSG_PW_MISMATCH = "La nueva contraseña y su confirmación no coinciden";
  var MSG_PW_SHORT = "La nueva contraseña debe tener al menos 12 caracteres";
  var MSG_PW_OK = "Contraseña actualizada; ingresa con la nueva";
  var MSG_PW_FAILED = "No fue posible cambiar la contraseña";
  var MSG_MISSING = "Completa todos los campos";

  function meta(name) {
    var el = document.querySelector('meta[name="' + name + '"]');
    return el ? el.getAttribute("content") || "" : "";
  }

  var SUPABASE_URL = meta("tc-supabase-url");
  var PUBLISHABLE_KEY = meta("tc-publishable-key");
  var CONFIGURED = /^https:\/\/[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(SUPABASE_URL) &&
                   /^sb_publishable_[A-Za-z0-9_-]+$/.test(PUBLISHABLE_KEY);

  function $(id) { return document.getElementById(id); }

  function say(el, text, kind) {
    el.textContent = text;
    el.className = "msg" + (kind ? " " + kind : "");
  }

  // Password grant de Supabase. Devuelve {status, accessToken}; accessToken solo si 200.
  function passwordGrant(email, password) {
    return fetch(SUPABASE_URL + "/auth/v1/token?grant_type=password", {
      method: "POST",
      headers: { "apikey": PUBLISHABLE_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, password: password }),
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer"
    }).then(function (res) {
      if (res.status !== 200) { return { status: res.status, accessToken: null }; }
      return res.json().then(function (data) {
        var token = data && typeof data.access_token === "string" ? data.access_token : null;
        return { status: token ? 200 : 0, accessToken: token };
      });
    });
  }

  // Envía el access_token a TradeCore con un formulario oculto (POST same-origin).
  function submitToTradeCore(accessToken) {
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/auth/enter";
    form.hidden = true;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "access_token";
    input.value = accessToken;
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
  }

  function onLogin(ev) {
    ev.preventDefault();
    var msg = $("login-msg");
    var button = $("login-submit");
    var email = $("login-email").value.trim();
    var password = $("login-password").value;
    if (!CONFIGURED) { say(msg, MSG_UNAVAILABLE, "err"); return; }
    if (!email || !password) { say(msg, MSG_MISSING, "err"); return; }
    button.disabled = true;
    say(msg, "", null);
    passwordGrant(email, password).then(function (r) {
      $("login-password").value = "";
      if (r.status === 200) { submitToTradeCore(r.accessToken); return; }
      button.disabled = false;
      say(msg, r.status === 400 ? MSG_BAD_CREDENTIALS : MSG_UNAVAILABLE, "err");
    }, function () {
      $("login-password").value = "";
      button.disabled = false;
      say(msg, MSG_UNAVAILABLE, "err");
    });
  }

  function onChangePassword(ev) {
    ev.preventDefault();
    var msg = $("pw-msg");
    var button = $("pw-submit");
    var email = $("pw-email").value.trim();
    var current = $("pw-current").value;
    var next = $("pw-new").value;
    var confirm = $("pw-confirm").value;
    function clearFields() {
      $("pw-current").value = ""; $("pw-new").value = ""; $("pw-confirm").value = "";
    }
    if (!CONFIGURED) { say(msg, MSG_UNAVAILABLE, "err"); return; }
    if (!email || !current || !next || !confirm) { say(msg, MSG_MISSING, "err"); return; }
    if (next.length < 12) { say(msg, MSG_PW_SHORT, "err"); return; }
    if (next !== confirm) { say(msg, MSG_PW_MISMATCH, "err"); return; }
    button.disabled = true;
    say(msg, "", null);
    passwordGrant(email, current).then(function (r) {
      if (r.status !== 200) {
        clearFields();
        button.disabled = false;
        say(msg, r.status === 400 ? MSG_BAD_CREDENTIALS : MSG_UNAVAILABLE, "err");
        return null;
      }
      return fetch(SUPABASE_URL + "/auth/v1/user", {
        method: "PUT",
        headers: {
          "apikey": PUBLISHABLE_KEY,
          "Authorization": "Bearer " + r.accessToken,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ password: next }),
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer"
      }).then(function (res) {
        clearFields();
        button.disabled = false;
        if (res.status === 200) { say(msg, MSG_PW_OK, "ok"); }
        else { say(msg, MSG_PW_FAILED, "err"); }
      });
    }).catch(function () {
      clearFields();
      button.disabled = false;
      say(msg, MSG_UNAVAILABLE, "err");
    });
  }

  function show(view) {
    $("view-login").hidden = view !== "login";
    $("view-password").hidden = view !== "password";
  }

  function init() {
    if (document.body.getAttribute("data-view") === "session") { return; }
    $("form-login").addEventListener("submit", onLogin);
    $("form-password").addEventListener("submit", onChangePassword);
    $("show-password").addEventListener("click", function () { show("password"); });
    $("show-login").addEventListener("click", function () { show("login"); });
    if (!CONFIGURED) {
      say($("login-msg"), MSG_UNAVAILABLE, "err");
      $("login-submit").disabled = true;
      $("pw-submit").disabled = true;
    } else if (new URLSearchParams(window.location.search).get("e") === "1") {
      say($("login-msg"), MSG_LOGIN_FAILED, "err");
    }
  }

  init();
})();
