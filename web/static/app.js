/* Localize Studio — tương tác phía client (vanilla JS, không framework) */

// ---- Theme sáng/tối (global, gọi từ nút trong HTML) ----
function lsTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('ls-theme', t); } catch (e) {}
}

(function () {
  "use strict";

  // ---- API key (nhúng từ server cho HTMX & fetch) ----
  function apiKey() {
    var b = document.body.getAttribute("hx-headers");
    try { return JSON.parse(b)["X-API-Key"] || ""; } catch (e) { return ""; }
  }
  function authHeaders() {
    var k = apiKey();
    return k ? { "X-API-Key": k } : {};
  }

  // ---- Health (poll /healthz) ----
  function pollHealth() {
    var el = document.getElementById("health");
    if (!el) return;
    fetch("/healthz").then(function (r) { return r.json(); }).then(function (d) {
      el.classList.add("ok"); el.classList.remove("bad");
      el.innerHTML = '<span class="dot"></span> ' +
        (d.running ? "Đang xử lý " + d.running : "Đã kết nối · worker chạy");
      var nc = document.getElementById("nav-count");
      if (nc) { var a = (d.queued || 0) + (d.running || 0); nc.textContent = a || "·"; }
    }).catch(function () {
      el.classList.add("bad"); el.classList.remove("ok");
      el.innerHTML = '<span class="dot"></span> Mất kết nối';
    });
  }
  pollHealth();
  setInterval(pollHealth, 5000);

  // ===== Trang dịch (index) =====
  var form = document.getElementById("job-form");
  if (!form) return;

  var currentSrc = "file";
  var currentMode = "translate";
  var currentCSource = "film";   // film | voice_ai (chỉ dùng trong mode create)

  var modeBtns = document.querySelectorAll(".mode-btn");
  var urlSeg = document.querySelector('.seg-btn[data-src="url"]');
  var sourceSection = document.getElementById("source-section");
  var pageTitle = document.getElementById("page-title");
  var pageSub = document.getElementById("page-sub");
  var submitLabel = document.getElementById("submit-label");

  // Khối upload video chỉ hiện khi: dịch, HOẶC tạo video kiểu "tự quay".
  function updateSourceVisibility() {
    var needUpload = currentMode === "translate" ||
      (currentMode === "create" && currentCSource === "film");
    if (sourceSection) sourceSection.classList.toggle("hidden", !needUpload);
  }

  // ---- Toggle chế độ: Dịch video / Tạo video ----
  function applyMode(m) {
    currentMode = m;
    modeBtns.forEach(function (b) { b.classList.toggle("active", b.dataset.mode === m); });
    document.querySelectorAll("[data-mode-block]").forEach(function (el) {
      el.classList.toggle("hidden", el.dataset.modeBlock !== m);
    });
    var create = m === "create";
    if (urlSeg) urlSeg.classList.toggle("hidden", create);   // tạo video không dán link
    if (create && currentSrc === "url") document.querySelector('.seg-btn[data-src="file"]').click();
    updateSourceVisibility();
    if (pageTitle) pageTitle.textContent = create ? "Tạo video" : "Dịch & lồng tiếng video";
    if (pageSub) pageSub.textContent = create
      ? "Tự quay (caption giọng thật) hoặc để AI lồng tiếng từ kịch bản — phụ đề karaoke động tự động."
      : "Tải lên file hoặc dán link video ngắn từ TikTok, Douyin, Instagram. Chọn dịch phụ đề, lồng tiếng, hay cả hai.";
    syncSubmitLabel();
  }
  function syncSubmitLabel() {
    if (!submitLabel) return;
    submitLabel.textContent = currentMode !== "create" ? "Bắt đầu dịch"
      : (currentCSource === "voice_ai" ? "Tạo video (AI lồng tiếng)" : "Tạo video");
  }
  modeBtns.forEach(function (b) {
    b.addEventListener("click", function () { applyMode(b.dataset.mode); });
  });

  // ---- Sub-toggle nguồn lời nói (trong mode create) ----
  document.querySelectorAll("#csource-seg .seg-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      currentCSource = b.dataset.csource;
      document.querySelectorAll("#csource-seg .seg-btn").forEach(function (x) {
        x.classList.toggle("active", x === b);
      });
      document.querySelectorAll("[data-csource-block]").forEach(function (el) {
        el.classList.toggle("hidden", el.dataset.csourceBlock !== currentCSource);
      });
      updateSourceVisibility();
      syncSubmitLabel();
    });
  });

  applyMode(form.getAttribute("data-init-mode") || "translate");

  // ---- Segment file/url ----
  document.querySelectorAll(".seg-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".seg-btn").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      currentSrc = btn.dataset.src;
      document.querySelectorAll(".src-panel").forEach(function (p) {
        p.classList.toggle("hidden", p.dataset.panel !== currentSrc);
      });
    });
  });

  // ---- Dropzone ----
  var dz = document.getElementById("dropzone");
  var fileInput = document.getElementById("file");
  var dzName = document.getElementById("dz-name");
  function showFile(f) { if (f) dzName.textContent = f.name + " (" + (f.size / 1048576).toFixed(1) + " MB)"; }
  if (dz) {
    fileInput.addEventListener("change", function () { showFile(fileInput.files[0]); });
    ["dragenter", "dragover"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("drag"); });
    });
    dz.addEventListener("drop", function (e) {
      if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(fileInput.files[0]); }
    });
  }

  // ---- Hiện/ẩn "dịch mọi chữ" theo checkbox OCR ----
  var ocrMain = document.getElementById("ocr_overlay");
  var ocrSub = document.getElementById("ocr-sub");
  if (ocrMain && ocrSub) {
    ocrMain.addEventListener("change", function () {
      ocrSub.classList.toggle("hidden", !ocrMain.checked);
      if (!ocrMain.checked) document.getElementById("ocr_all_text").checked = false;
    });
  }

  // ---- Hiện/ẩn tuỳ chọn giọng theo choice ----
  var voiceOpts = document.getElementById("voice-opts");
  function syncVoiceOpts() {
    var c = form.querySelector('input[name=choice]:checked').value;
    voiceOpts.classList.toggle("hidden", c === "text");
  }
  form.querySelectorAll('input[name=choice]').forEach(function (r) {
    r.addEventListener("change", syncVoiceOpts);
  });
  syncVoiceOpts();

  // ---- Lọc giọng ----
  var search = document.getElementById("voice-search");
  var voiceSel = document.getElementById("voice");
  var voiceCount = document.getElementById("voice-count");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.toLowerCase().trim();
      var shown = 0;
      Array.prototype.forEach.call(voiceSel.options, function (o) {
        var hay = (o.value + " " + o.dataset.gender).toLowerCase();
        var ok = !q || hay.indexOf(q) >= 0;
        o.hidden = !ok; if (ok) shown++;
      });
      voiceCount.textContent = shown + " giọng";
    });
  }

  // ---- Sliders ----
  var rateSlider = document.getElementById("rate-slider");
  var rateHidden = document.getElementById("rate");
  var rateVal = document.getElementById("rate-val");
  if (rateSlider) {
    rateSlider.addEventListener("input", function () {
      var v = parseInt(rateSlider.value, 10);
      var s = (v >= 0 ? "+" : "") + v + "%";
      rateHidden.value = s; rateVal.textContent = s;
    });
  }
  var kov = document.getElementById("kov");
  var kovVal = document.getElementById("kov-val");
  if (kov) kov.addEventListener("input", function () { kovVal.textContent = kov.value; });

  // ---- Submit ----
  var btn = document.getElementById("submit-btn");
  var msg = document.getElementById("form-msg");
  function setMsg(t, cls) { msg.textContent = t; msg.className = "form-msg " + (cls || ""); }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    btn.disabled = true;
    setMsg("Đang gửi…", "");

    var projSel = document.getElementById("project_id");

    // ===== Chế độ TẠO VIDEO =====
    if (currentMode === "create") {
      // --- B. AI lồng tiếng từ kịch bản ---
      if (currentCSource === "voice_ai") {
        var script = (document.getElementById("create-script") || {}).value || "";
        if (!script.trim()) { setMsg("Hãy nhập kịch bản.", "err"); btn.disabled = false; return; }
        fetch("/api/jobs/create-voice", {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
          body: JSON.stringify({
            script: script,
            voice: (document.getElementById("create-voice") || {}).value || "vi-VN-HoaiMyNeural",
            terms: (document.getElementById("create-terms") || {}).value || "",
            project_id: projSel ? projSel.value : ""
          })
        })
          .then(function (r) { if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.status); }); return r.json(); })
          .then(function (d) { setMsg("Đã tạo job, đang chuyển…", "ok"); window.location.href = "/jobs/" + d.id; })
          .catch(function (err) { setMsg("Lỗi: " + err.message, "err"); btn.disabled = false; });
        return;
      }
      // --- A. Tự quay (caption giọng thật) ---
      if (!fileInput.files.length) { setMsg("Hãy chọn video bạn tự quay.", "err"); btn.disabled = false; return; }
      var cfd = new FormData();
      cfd.append("file", fileInput.files[0]);
      cfd.append("terms", (document.getElementById("create-terms") || {}).value || "");
      cfd.append("lang", (document.getElementById("create-lang") || {}).value || "vi");
      cfd.append("model", (document.getElementById("create-model") || {}).value || "base");
      cfd.append("project_id", projSel ? projSel.value : "");
      fetch("/api/jobs/create", { method: "POST", headers: authHeaders(), body: cfd })
        .then(function (r) { if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.status); }); return r.json(); })
        .then(function (d) { setMsg("Đã tạo job, đang chuyển…", "ok"); window.location.href = "/jobs/" + d.id; })
        .catch(function (err) { setMsg("Lỗi: " + err.message, "err"); btn.disabled = false; });
      return;
    }

    // ===== Chế độ DỊCH (đường cũ) =====
    var choice = form.querySelector('input[name=choice]:checked').value;
    var common = {
      choice: choice,
      ocr_overlay: document.getElementById("ocr_overlay").checked,
      ocr_all_text: document.getElementById("ocr_all_text").checked,
      target_language: form.target_language.value,
      source_language: form.source_language ? form.source_language.value : "",
      voice: voiceSel ? voiceSel.value : "vi-VN-HoaiMyNeural",
      rate: rateHidden ? rateHidden.value : "+0%",
      keep_original_volume: kov ? parseFloat(kov.value) : null,
      project_id: projSel ? projSel.value : ""
    };

    var req;
    if (currentSrc === "url") {
      var url = document.getElementById("url").value.trim();
      if (!url) { setMsg("Hãy nhập link video.", "err"); btn.disabled = false; return; }
      req = fetch("/api/jobs/url", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify(Object.assign({ url: url }, common))
      });
    } else {
      if (!fileInput.files.length) { setMsg("Hãy chọn file video.", "err"); btn.disabled = false; return; }
      var fd = new FormData();
      fd.append("file", fileInput.files[0]);
      Object.keys(common).forEach(function (k) {
        if (common[k] !== null && common[k] !== undefined) fd.append(k, common[k]);
      });
      req = fetch("/api/jobs/upload", { method: "POST", headers: authHeaders(), body: fd });
    }

    req.then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.status); });
      return r.json();
    }).then(function (d) {
      setMsg("Đã tạo job, đang chuyển…", "ok");
      window.location.href = "/jobs/" + d.id;
    }).catch(function (err) {
      setMsg("Lỗi: " + err.message, "err");
      btn.disabled = false;
    });
  });
})();
