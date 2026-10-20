// Review screen: review timer, keyboard shortcuts, copying the question for the client contact,
// and the unit dropdown following the chosen product. The review area is replaced after every
// change, so everything here looks elements up when it needs them.
(function () {
  var opened = Date.now();

  function timerTick() {
    var el = document.getElementById("review-timer");
    if (!el) return;
    var seconds = Math.floor((Date.now() - opened) / 1000);
    var m = Math.floor(seconds / 60), s = seconds % 60;
    el.textContent = m + ":" + (s < 10 ? "0" : "") + s;
  }
  setInterval(timerTick, 1000);
  timerTick();

  // Send the time spent on this page with the approval.
  document.body.addEventListener("htmx:configRequest", function (event) {
    if (/\/approve$/.test(event.detail.path)) {
      event.detail.parameters.seconds = String(Math.floor((Date.now() - opened) / 1000));
    }
  });
  document.body.addEventListener("submit", function (event) {
    if (event.target.id === "approve-form") {
      event.target.querySelector("input[name=seconds]").value = String(Math.floor((Date.now() - opened) / 1000));
    }
  });

  // The unit dropdown shows the units of whichever product is picked.
  document.body.addEventListener("change", function (event) {
    var radio = event.target;
    if (radio.name !== "product_code") return;
    var form = radio.closest("form");
    var select = form && form.querySelector(".unit-select");
    if (!select) return;
    var sell = select.querySelector("option[value=sell]");
    var inner = select.querySelector("option[value=inner]");
    if (sell && radio.dataset.sell) sell.textContent = radio.dataset.sell;
    if (radio.dataset.inner) {
      if (!inner) {
        inner = document.createElement("option");
        inner.value = "inner";
        select.appendChild(inner);
      }
      inner.textContent = radio.dataset.inner;
    } else if (inner) {
      if (select.value === "inner") select.value = "sell";
      inner.remove();
    }
  });

  // Enter in the catalogue search should search, not save the line.
  document.body.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && event.target.matches("input[type=search]")) event.preventDefault();
  });

  function copyQuestion() {
    var text = document.getElementById("ask-text");
    var status = document.getElementById("copied");
    var button = document.getElementById("ask-client");
    if (!text) return;
    var done = function () { if (status) status.textContent = "Question copied. Paste it into an email to the client contact."; };
    if (navigator.clipboard && window.isSecureContext !== false) {
      navigator.clipboard.writeText(text.value).then(done, function () { window.location.href = button.dataset.mailto; });
    } else {
      window.location.href = button.dataset.mailto;
    }
  }
  document.body.addEventListener("click", function (event) {
    if (event.target.closest("#ask-client")) copyQuestion();
  });

  function typing(target) {
    return target.closest("input, textarea, select, [contenteditable]") !== null;
  }

  function currentLineForm() {
    var active = document.activeElement;
    var form = active && active.closest ? active.closest("form.line-edit") : null;
    return form || document.querySelector("form.line-edit");
  }

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      var open = currentLineForm();
      if (open) {
        var row = open.closest("tr");
        if (row) row.hidden = true;
        event.preventDefault();
      }
      return;
    }
    if (typing(event.target) && !(event.target.type === "radio" || event.target.type === "checkbox")) return;
    var key = event.key.toLowerCase();
    if (key === "a") {
      var approve = document.getElementById("approve");
      if (approve && !approve.disabled) { event.preventDefault(); approve.click(); }
    } else if (key === "n") {
      var next = document.getElementById("next-order");
      if (next) { event.preventDefault(); window.location.href = next.href; }
    } else if (/^[1-5]$/.test(key)) {
      var form = currentLineForm();
      if (!form) return;
      var radios = form.querySelectorAll(".choices input[type=radio]");
      var pick = radios[Number(key) - 1];
      if (!pick) return;
      event.preventDefault();
      pick.checked = true;
      pick.dispatchEvent(new Event("change", { bubbles: true }));
      if (window.htmx) htmx.trigger(form, "submit"); else form.submit();
    }
  });
})();
