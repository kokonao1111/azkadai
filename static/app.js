(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  const post = async (url, body) => {
    const res = await fetch(url, { method: "POST", body, headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "うまくいきませんでした");
    return data;
  };
  const form = (obj) => {
    const f = new FormData();
    Object.entries(obj).forEach(([k, v]) => f.append(k, v));
    return f;
  };

  /* ---- つくる ---- */
  const dlg = $("#make");
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-open=make]")) {
      dlg.showModal();
      $("input[name=text]", dlg).focus();
    }
    if (e.target.closest("[data-close]") || e.target === dlg) dlg.close();
  });

  $$(".m-pic input", dlg).forEach((inp) =>
    inp.addEventListener("change", () => inp.parentElement.classList.toggle("has", inp.files.length > 0))
  );

  const makeForm = $("#makeForm");
  const savedName = localStorage.getItem("dotchi:name");
  if (savedName) $("input[name=name]", makeForm).value = savedName;

  makeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $(".m-send", makeForm);
    const err = $("#makeErr");
    err.hidden = true;
    btn.disabled = true;
    localStorage.setItem("dotchi:name", $("input[name=name]", makeForm).value.trim());
    try {
      const d = await post("/new", new FormData(makeForm));
      location.href = "/q/" + d.id;
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      btn.disabled = false;
    }
  });

  /* ---- 投票（綱引き） ---- */
  const showError = (card, msg) => {
    let p = $(".card-err", card);
    if (!p) {
      p = document.createElement("p");
      p.className = "err card-err";
      p.style.margin = "10px 0 0";
      card.append(p);
    }
    p.textContent = msg;
  };

  const paint = (card, d) => {
    card.classList.add("voted");
    card.dataset.mine = d.mine;
    const rope = $(".rope", card);
    requestAnimationFrame(() => rope.style.setProperty("--pa", d.pa));
    $(".sa .pct", card).innerHTML = `${d.pa}<small>%</small>`;
    $(".sb .pct", card).innerHTML = `${d.pb}<small>%</small>`;
    $(".sa", card).classList.toggle("me", d.mine === "a");
    $(".sb", card).classList.toggle("me", d.mine === "b");
    $(".total", card).textContent = d.total;
    const st = $(".sticker", card);
    st.textContent = d.stance ? d.stance + "！" : "";
    st.classList.toggle("hidden", !d.stance);
    card.classList.add("pop");
    setTimeout(() => card.classList.remove("pop"), 400);
  };

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".card [data-side]");
    if (!btn || btn.disabled) return;
    const card = btn.closest(".card");
    if (card.classList.contains("voted") || card.dataset.busy) return;
    card.dataset.busy = "1";
    try {
      const d = await post(`/q/${card.dataset.id}/vote`, form({ side: btn.dataset.side }));
      paint(card, d);
      // 詳細ページでは投票するとコメント欄が開くので読み直す
      if ($("#say")) setTimeout(() => location.reload(), 900);
    } catch (ex) {
      showError(card, ex.message);
    } finally {
      delete card.dataset.busy;
    }
  });

  /* ---- 寝返る ---- */
  const turn = $(".turn-btn");
  if (turn) {
    turn.addEventListener("click", async () => {
      turn.disabled = true;
      try {
        await post(`/q/${$(".card").dataset.id}/vote`, form({ side: turn.dataset.side }));
        location.reload();
      } catch (ex) {
        turn.textContent = ex.message;
      }
    });
  }

  /* ---- 言い合い ---- */
  const say = $("#say");
  if (say && !say.classList.contains("locked")) {
    const id = $(".debate").dataset.id;
    say.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("input", say);
      const err = $("#sayErr");
      const btn = $("button", say);
      err.hidden = true;
      btn.disabled = true;
      try {
        const d = await post(`/q/${id}/comment`, new FormData(say));
        const col = $(d.side === "a" ? ".debate .ca" : ".debate .cb");
        const li = document.createElement("li");
        li.className = "new";
        li.textContent = d.body;
        $("ul", col).append(li);
        const n = $("h3 span", col);
        n.textContent = parseInt(n.textContent, 10) + 1;
        input.value = "";
        li.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      } finally {
        btn.disabled = false;
      }
    });
  }

  /* ---- 通報・削除 ---- */
  const report = $("#reportBtn");
  if (report) {
    report.addEventListener("click", async () => {
      report.disabled = true;
      try {
        await post(`/q/${$(".card").dataset.id}/report`);
        report.textContent = "通報しました";
      } catch (ex) {
        report.textContent = ex.message;
      }
    });
  }

  const delForm = $("#delForm");
  if (delForm) {
    delForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("input", delForm);
      try {
        await post(`/q/${$(".card").dataset.id}/delete`, new FormData(delForm));
        location.href = "/";
      } catch (ex) {
        input.value = "";
        input.placeholder = ex.message;
      }
    });
  }
})();
