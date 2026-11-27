(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* ---- 黒板消しまでのカウントダウン ---- */
  const cd = $(".countdown");
  if (cd) {
    const end = new Date(cd.dataset.erase).getTime();
    const pad = (n) => String(n).padStart(2, "0");
    const tick = () => {
      let s = Math.max(0, Math.floor((end - Date.now()) / 1000));
      if (s === 0) { location.reload(); return; }
      const h = Math.floor(s / 3600); s %= 3600;
      cd.textContent = `${pad(h)}:${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ---- チョーク置き場をひらく ---- */
  const tray = $("#tray");
  const panel = $("#trayPanel");
  const openBtn = $("#openTray");
  const textarea = tray && $("textarea", tray);

  const openTray = (focus = true) => {
    panel.hidden = false;
    tray.classList.add("open");
    openBtn.setAttribute("aria-expanded", "true");
    $("span", openBtn).textContent = "とじる";
    if (focus) {
      textarea.focus({ preventScroll: true });
      tray.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  };
  const closeTray = () => {
    panel.hidden = true;
    tray.classList.remove("open");
    openBtn.setAttribute("aria-expanded", "false");
    $("span", openBtn).textContent = "書く";
  };

  if (tray) {
    openBtn.addEventListener("click", () => (panel.hidden ? openTray() : closeTray()));
    $$(".stick input", tray).forEach((r) =>
      r.addEventListener("change", () => {
        if (panel.hidden) openTray();
        textarea.style.color = getComputedStyle(r.nextElementSibling).backgroundColor;
      })
    );
    if (document.documentElement.dataset.openTray) openTray(false);

    const fileInput = $('input[type="file"]', tray);
    fileInput.addEventListener("change", () => {
      $("#fileName").textContent = fileInput.files[0]?.name || "えらんでない";
    });

    const count = $("#count");
    textarea.addEventListener("input", () => {
      count.textContent = textarea.value.length;
      count.parentElement.classList.toggle("over", textarea.value.length > 140);
    });

    // 前に使った名前を覚えておく
    const nameInput = $('input[name="name"]', tray);
    nameInput.value = localStorage.getItem("kokuban:name") || "";

    tray.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $("#formErr");
      const btn = $(".write-btn", tray);
      err.hidden = true;
      btn.disabled = true;
      btn.textContent = "書いてる…";
      localStorage.setItem("kokuban:name", nameInput.value.trim());
      try {
        const res = await fetch("/post", {
          method: "POST",
          body: new FormData(tray),
          headers: { Accept: "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "うまく書けませんでした。");
        sessionStorage.setItem("kokuban:fresh", data.post.RowKey);
        if (location.pathname === "/" && !location.search) {
          location.hash = "p-" + data.post.RowKey;
          location.reload();
        } else {
          location.href = "/#p-" + data.post.RowKey;
        }
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        btn.disabled = false;
        btn.textContent = "黒板に書く";
      }
    });
  }

  /* ---- 書いたばかりの文字はチョークで書くように出す ---- */
  const fresh = sessionStorage.getItem("kokuban:fresh");
  if (fresh) {
    sessionStorage.removeItem("kokuban:fresh");
    const el = document.getElementById("p-" + fresh);
    if (el) {
      el.classList.add("fresh");
      el.scrollIntoView({ block: "center" });
    }
  }

  /* ---- 書き込みごとのボタン ---- */
  const tallied = new Set(JSON.parse(localStorage.getItem("kokuban:tally") || "[]"));
  $$(".writing").forEach((w) => {
    if (tallied.has(w.dataset.row)) $(".tally-btn", w).classList.add("done");
  });

  const post = async (url, body) => {
    const res = await fetch(url, { method: "POST", body, headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "うまくいきませんでした。");
    return data;
  };

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const w = btn.closest(".writing");
    const row = w.dataset.row;
    const act = btn.dataset.act;

    if (act === "tally") {
      if (tallied.has(row)) return;
      tallied.add(row);
      localStorage.setItem("kokuban:tally", JSON.stringify([...tallied]));
      btn.classList.add("done", "pop");
      try {
        const d = await post(`/post/${row}/tally`);
        $(".tally", btn).textContent = d.mark;
      } catch (_) { /* だめでも気にしない */ }
      setTimeout(() => btn.classList.remove("pop"), 400);
    }

    if (act === "reply") {
      const f = $(".reply-form", w);
      f.hidden = !f.hidden;
      if (!f.hidden) $("input", f).focus();
    }

    if (act === "more") {
      const m = $(".more-box", w);
      m.hidden = !m.hidden;
    }

    if (act === "report") {
      btn.disabled = true;
      try {
        const d = await post(`/post/${row}/report`);
        btn.textContent = d.hidden ? "見えなくしました" : "伝えました。ありがとう";
        if (d.hidden) w.style.opacity = ".25";
      } catch (ex) {
        btn.textContent = ex.message;
      }
    }
  });

  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (!f.matches(".reply-form, .erase-form")) return;
    e.preventDefault();
    const w = f.closest(".writing");
    const row = w.dataset.row;
    const button = $("button", f);
    button.disabled = true;
    try {
      if (f.matches(".reply-form")) {
        const d = await post(`/post/${row}/reply`, new FormData(f));
        let ul = $(".replies", w);
        if (!ul) {
          ul = document.createElement("ul");
          ul.className = "replies";
          $(".meta", w).after(ul);
        }
        const li = document.createElement("li");
        const arrow = document.createElement("span");
        arrow.className = "arrow";
        arrow.textContent = "↳";
        li.append(arrow, d.reply.body);
        ul.append(li);
        f.reset();
        f.hidden = true;
      } else {
        await post(`/post/${row}/erase`, new FormData(f));
        w.style.transition = "opacity .6s, filter .6s";
        w.style.opacity = "0";
        w.style.filter = "blur(4px)";
        setTimeout(() => w.remove(), 650);
      }
    } catch (ex) {
      const input = $("input", f);
      input.value = "";
      input.placeholder = ex.message;
    } finally {
      button.disabled = false;
    }
  });

  /* ---- 新しい書き込みが来たらお知らせ ---- */
  const firstRow = $(".writing")?.dataset.row || null;
  const note = $("#freshNote");
  const check = async () => {
    if (document.hidden) return;
    try {
      const d = await (await fetch("/api/board")).json();
      if (d.latest && d.latest !== firstRow) note.hidden = false;
    } catch (_) {}
  };
  if (note && !location.search) setInterval(check, 30000);
})();
