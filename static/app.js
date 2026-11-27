(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  // 消えるまでの残り時間
  const cd = $(".countdown");
  const bar = $(".life");
  if (cd) {
    const end = new Date(cd.dataset.reset).getTime();
    const pad = (n) => String(n).padStart(2, "0");
    const tick = () => {
      const ms = end - Date.now();
      if (ms <= 0) return location.reload();
      const s = Math.floor(ms / 1000);
      cd.textContent = `${Math.floor(s / 3600)}時間${pad(Math.floor((s % 3600) / 60))}分`;
      if (bar) bar.style.setProperty("--left", (ms / 86400000).toFixed(4));
    };
    tick();
    setInterval(tick, 30000);
  }

  // 投稿フォーム
  const form = $("#composer");
  if (form) {
    const textarea = $("textarea", form);
    const count = $("#count");
    textarea.addEventListener("input", () => {
      count.textContent = textarea.value.length;
      count.parentElement.classList.toggle("over", textarea.value.length > 140);
    });

    const fileInput = $('input[type="file"]', form);
    fileInput.addEventListener("change", () => {
      $("#fileName").textContent = fileInput.files[0]?.name || "";
    });

    const nameInput = $('input[name="name"]', form);
    nameInput.value = localStorage.getItem("kyoudake:name") || "";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $("#formErr");
      const btn = $(".send", form);
      err.hidden = true;
      btn.disabled = true;
      localStorage.setItem("kyoudake:name", nameInput.value.trim());
      try {
        const res = await fetch("/post", {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "投稿できませんでした。");
        sessionStorage.setItem("kyoudake:fresh", data.post.RowKey);
        location.href = "/";
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        btn.disabled = false;
      }
    });
  }

  // 投稿した直後は自分の投稿を少し光らせる
  const fresh = sessionStorage.getItem("kyoudake:fresh");
  if (fresh) {
    sessionStorage.removeItem("kyoudake:fresh");
    document.getElementById("p-" + fresh)?.classList.add("fresh-post");
  }

  // わかる（同じ投稿には1回だけ）
  const liked = new Set(JSON.parse(localStorage.getItem("kyoudake:liked") || "[]"));
  $$(".post").forEach((p) => {
    if (liked.has(p.dataset.row)) $(".like", p).classList.add("done");
  });

  const send = async (url, body) => {
    const res = await fetch(url, { method: "POST", body, headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "うまくいきませんでした。");
    return data;
  };

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const post = btn.closest(".post");
    const row = post.dataset.row;

    switch (btn.dataset.act) {
      case "like": {
        if (liked.has(row)) return;
        liked.add(row);
        localStorage.setItem("kyoudake:liked", JSON.stringify([...liked]));
        btn.classList.add("done");
        const n = $(".n", btn);
        n.textContent = (parseInt(n.textContent, 10) || 0) + 1;
        try {
          const d = await send(`/post/${row}/like`);
          n.textContent = d.likes;
        } catch (_) {}
        break;
      }
      case "reply": {
        const f = $(".reply-form", post);
        f.hidden = !f.hidden;
        if (!f.hidden) $("input", f).focus();
        break;
      }
      case "more":
        $(".more-box", post).hidden ^= true;
        break;
      case "report":
        btn.disabled = true;
        try {
          const d = await send(`/post/${row}/report`);
          btn.textContent = "通報しました";
          if (d.hidden) post.style.opacity = ".3";
        } catch (ex) {
          btn.textContent = ex.message;
        }
        break;
    }
  });

  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (!f.matches(".reply-form, .delete-form")) return;
    e.preventDefault();
    const post = f.closest(".post");
    const row = post.dataset.row;
    const button = $("button", f);
    button.disabled = true;
    try {
      if (f.matches(".reply-form")) {
        const d = await send(`/post/${row}/reply`, new FormData(f));
        let ul = $(".replies", post);
        if (!ul) {
          ul = document.createElement("ul");
          ul.className = "replies";
          $(".acts", post).before(ul);
        }
        const li = document.createElement("li");
        const t = document.createElement("span");
        t.className = "time";
        t.textContent = new Date(d.reply.created).toTimeString().slice(0, 5);
        li.append(t, d.reply.body);
        ul.append(li);
        f.reset();
        f.hidden = true;
      } else {
        await send(`/post/${row}/delete`, new FormData(f));
        post.remove();
      }
    } catch (ex) {
      const input = $("input", f);
      input.value = "";
      input.placeholder = ex.message;
    } finally {
      button.disabled = false;
    }
  });

  // 新しい投稿が来たらお知らせ
  const firstRow = $(".post")?.dataset.row || null;
  const note = $("#freshNote");
  if (note && !location.search) {
    setInterval(async () => {
      if (document.hidden) return;
      try {
        const d = await (await fetch("/api/latest")).json();
        if (d.latest && d.latest !== firstRow) note.hidden = false;
      } catch (_) {}
    }, 30000);
  }
})();
