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

  /* ---- スマホ表示に切りかえ ---- */
  const toPhone = $("#toPhone");
  if (toPhone) {
    toPhone.addEventListener("click", () => {
      sessionStorage.removeItem("dotchi:view");
      location.href = "/phone?u=" + encodeURIComponent(location.pathname + location.search);
    });
  }

  /* ---- チュートリアル ---- */
  const TOUR = [
    { sel: ".card .rope", title: "どっちか、タップするだけ", text: "押すまでみんなの結果は見えません。押した瞬間に綱引きが動いて、少数派か多数派かがわかります。" },
    { sel: ".card .talk", title: "言い合いに参加", text: "コメントは自分が選んだ側の列に並びます。相手の意見で気が変わったら、1回だけ寝返れます。" },
    { sel: ".you", title: "あなたは何派？", text: "3問投票すると判定スタート。少数派になった割合で、王道派〜ひねくれ派のどこにいるかが決まります。" },
    { sel: ".make-btn", title: "二択をつくる", text: "お題とAとBを書くだけ。写真をつければ「どっちの服がいい？」みたいな勝負もできます。" },
  ];

  const startTour = () => {
    const steps = TOUR.filter((s) => $(s.sel));
    if (!steps.length) return;
    let i = 0;
    const block = document.createElement("div");
    block.className = "tour-block";
    const spot = document.createElement("div");
    spot.className = "tour-spot";
    const tip = document.createElement("div");
    tip.className = "tour-tip";
    tip.setAttribute("role", "dialog");
    document.body.append(block, spot, tip);

    const end = () => {
      localStorage.setItem("dotchi:tour", "done");
      block.remove(); spot.remove(); tip.remove();
      window.removeEventListener("resize", place);
    };

    const place = () => {
      const step = steps[i];
      const el = $(step.sel);
      const r = el.getBoundingClientRect();
      const pad = 6;
      Object.assign(spot.style, {
        top: r.top - pad + "px", left: r.left - pad + "px",
        width: r.width + pad * 2 + "px", height: r.height + pad * 2 + "px",
      });
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      const below = r.bottom + 14 + th < innerHeight;
      const top = below ? r.bottom + 14 : Math.max(12, r.top - th - 14);
      const left = Math.min(Math.max(12, r.left + r.width / 2 - tw / 2), innerWidth - tw - 12);
      tip.style.top = top + "px";
      tip.style.left = left + "px";
    };

    const show = () => {
      const step = steps[i];
      const last = i === steps.length - 1;
      tip.innerHTML = `
        <span class="tour-step">${i + 1} / ${steps.length}</span>
        <h3></h3><p></p>
        <div class="tour-nav">
          <button type="button" class="tour-skip">スキップ</button>
          <span class="tour-dots">${steps.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("")}</span>
          <button type="button" class="tour-next${last ? " last" : ""}">${last ? "はじめる" : "次へ"}</button>
        </div>`;
      $("h3", tip).textContent = step.title;
      $("p", tip).textContent = step.text;
      $(".tour-skip", tip).onclick = end;
      $(".tour-next", tip).onclick = () => (last ? end() : (i++, show()));
      const el = $(step.sel);
      const r = el.getBoundingClientRect();
      if (r.top < 70 || r.bottom > innerHeight - 200) {
        el.scrollIntoView({ block: "center" });
      }
      requestAnimationFrame(place);
    };

    window.addEventListener("resize", place);
    show();
  };

  const wantTour = new URLSearchParams(location.search).has("tour");
  if ($(".card") && (wantTour || !localStorage.getItem("dotchi:tour"))) {
    setTimeout(startTour, wantTour ? 300 : 700);
  }

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
  const nameIn = $("input[name=name]", makeForm);
  if (savedName && nameIn) nameIn.value = savedName;

  makeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $(".m-send", makeForm);
    const err = $("#makeErr");
    err.hidden = true;
    btn.disabled = true;
    if (nameIn) localStorage.setItem("dotchi:name", nameIn.value.trim());
    try {
      const d = await post("/new", new FormData(makeForm));
      location.href = "/q/" + d.id + "?made=1";
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

  /* ---- お問い合わせ ---- */
  const ct = $("#contactForm");
  if (ct) {
    const area = $("textarea", ct);
    area.addEventListener("input", () => ($("#ctCount").textContent = area.value.length));
    ct.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $("#ctErr");
      const btn = $("#ctSend");
      err.hidden = true;
      btn.disabled = true;
      btn.textContent = "送信中…";
      try {
        const d = await post("/contact", new FormData(ct));
        $("#ctNo").textContent = d.no;
        $("#ctKind").textContent = new FormData(ct).get("kind");
        ct.hidden = true;
        $("#contactDone").hidden = false;
        scrollTo(0, 0);
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      } finally {
        btn.disabled = false;
        btn.textContent = "送信する";
      }
    });
    $("#ctAgain").addEventListener("click", () => {
      ct.reset();
      $("#ctCount").textContent = 0;
      $("#contactDone").hidden = true;
      ct.hidden = false;
    });
  }

  /* ---- マイページ：削除キーを見る・二択を消す ---- */
  $$(".mine-bar").forEach((bar) => {
    const code = $(".key", bar);
    $(".key-show", bar).addEventListener("click", (e) => {
      const open = code.textContent !== code.dataset.key;
      code.textContent = open ? code.dataset.key : "••••";
      e.target.textContent = open ? "かくす" : "見る";
    });
    const del = $(".key-del", bar);
    del.addEventListener("click", async () => {
      if (!del.dataset.sure) {
        del.dataset.sure = "1";
        del.textContent = "本当に消す？";
        del.classList.add("sure");
        return;
      }
      del.disabled = true;
      try {
        await post(`/q/${bar.dataset.id}/delete`, form({ key: code.dataset.key }));
        bar.closest(".mine-wrap").hidden = true;
      } catch (ex) {
        del.textContent = ex.message;
      }
    });
  });

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
