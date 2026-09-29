/* Botzi chat widget. Include with:
 *   <script src="https://<botul-tau>/widget.js" async></script>
 * Optional attributes on the script tag: data-color="#2446D8" data-title="Asistent"
 */
(function () {
  var script = document.currentScript;
  var base = new URL(script.src).origin;
  var color = script.getAttribute("data-color") || "#2446D8";
  var title = script.getAttribute("data-title") || "Asistent";
  var storageKey = "botzi-session-" + base;
  var sessionId = null;
  try { sessionId = localStorage.getItem(storageKey); } catch (e) {}

  var css = "\
.bz-btn{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;background:" + color + ";color:#fff;box-shadow:0 10px 30px rgba(0,0,0,.25);cursor:pointer;z-index:99998;font-size:24px;display:grid;place-items:center}\
.bz-panel{position:fixed;right:20px;bottom:88px;width:min(360px,calc(100vw - 40px));height:min(520px,calc(100vh - 120px));background:#fff;border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.3);display:none;flex-direction:column;overflow:hidden;z-index:99999;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#131c26}\
.bz-panel.open{display:flex}\
.bz-head{background:" + color + ";color:#fff;padding:14px 16px;font-weight:600;display:flex;justify-content:space-between;align-items:center}\
.bz-head button{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer}\
.bz-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;background:#f4f6f9}\
.bz-m{max-width:85%;padding:9px 13px;border-radius:16px;white-space:pre-wrap;word-wrap:break-word}\
.bz-m.bot{background:#fff;border-bottom-left-radius:5px;align-self:flex-start;box-shadow:0 1px 2px rgba(0,0,0,.08)}\
.bz-m.me{background:" + color + ";color:#fff;border-bottom-right-radius:5px;align-self:flex-end}\
.bz-m.typing{color:#6b7684;font-style:italic}\
.bz-form{display:flex;gap:8px;padding:10px;border-top:1px solid #e3e7ec;background:#fff}\
.bz-form input{flex:1;font:inherit;padding:10px 12px;border:1px solid #d5dbe2;border-radius:10px;outline:none}\
.bz-form input:focus{border-color:" + color + "}\
.bz-form button{font:inherit;font-weight:600;background:" + color + ";color:#fff;border:0;border-radius:10px;padding:0 14px;cursor:pointer}\
.bz-foot{font-size:11px;color:#8a94a0;text-align:center;padding:4px 0 8px;background:#fff}";

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var btn = document.createElement("button");
  btn.className = "bz-btn";
  btn.setAttribute("aria-label", "Deschide chatul");
  btn.innerHTML = "&#128172;";

  var panel = document.createElement("div");
  panel.className = "bz-panel";
  panel.innerHTML =
    '<div class="bz-head"><span class="bz-title"></span><button type="button" aria-label="Închide">&times;</button></div>' +
    '<div class="bz-msgs"></div>' +
    '<form class="bz-form"><input type="text" placeholder="Scrie un mesaj..." autocomplete="off" maxlength="2000"><button type="submit">Trimite</button></form>' +
    '<div class="bz-foot">asistent virtual</div>';

  document.body.appendChild(btn);
  document.body.appendChild(panel);

  var msgs = panel.querySelector(".bz-msgs");
  var form = panel.querySelector("form");
  var input = panel.querySelector("input");
  var greeted = false;
  panel.querySelector(".bz-title").textContent = title;

  function add(text, who) {
    var el = document.createElement("div");
    el.className = "bz-m " + who;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  function greet() {
    if (greeted) return;
    greeted = true;
    fetch(base + "/widget-config").then(function (r) { return r.json(); }).then(function (c) {
      if (c.business_name && !script.getAttribute("data-title")) panel.querySelector(".bz-title").textContent = c.business_name;
      add(c.greeting || "Bună! Cu ce te pot ajuta?", "bot");
    }).catch(function () { add("Bună! Cu ce te pot ajuta?", "bot"); });
  }

  btn.addEventListener("click", function () {
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) { greet(); input.focus(); }
  });
  panel.querySelector(".bz-head button").addEventListener("click", function () { panel.classList.remove("open"); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    add(text, "me");
    var typing = add("scrie...", "bot typing");
    fetch(base + "/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId })
    }).then(function (r) { return r.json(); }).then(function (data) {
      typing.remove();
      if (data.session_id && data.session_id !== sessionId) {
        sessionId = data.session_id;
        try { localStorage.setItem(storageKey, sessionId); } catch (e) {}
      }
      add(data.reply || "Nu am putut răspunde acum. Încearcă din nou.", "bot");
    }).catch(function () {
      typing.remove();
      add("Nu am putut trimite mesajul. Verifică conexiunea și încearcă din nou.", "bot");
    });
  });
})();
