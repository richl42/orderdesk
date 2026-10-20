// Order queue: drag and drop several files, upload straight after choosing, open and close the paste box.
(function () {
  var zone = document.getElementById("dropzone");
  var form = document.getElementById("upload-form");
  var input = document.getElementById("order-files");
  if (!zone || !form || !input) return;

  function send() {
    if (!input.files || !input.files.length) return;
    if (window.htmx) {
      htmx.trigger(form, "submit");
    } else {
      form.submit();
    }
  }

  input.addEventListener("change", send);
  form.addEventListener("htmx:afterRequest", function () { input.value = ""; });

  ["dragenter", "dragover"].forEach(function (name) {
    zone.addEventListener(name, function (event) {
      event.preventDefault();
      zone.classList.add("dropzone-over");
    });
  });
  ["dragleave", "drop"].forEach(function (name) {
    zone.addEventListener(name, function (event) {
      event.preventDefault();
      if (name === "dragleave" && zone.contains(event.relatedTarget)) return;
      zone.classList.remove("dropzone-over");
    });
  });
  zone.addEventListener("drop", function (event) {
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    send();
  });

  // The "Choose files" label works with the keyboard too.
  document.querySelectorAll("label[for='order-files']").forEach(function (label) {
    label.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
  });

  document.querySelectorAll("[data-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      var box = document.getElementById(button.getAttribute("data-open"));
      if (!box) return;
      box.hidden = false;
      var first = box.querySelector("textarea, input");
      if (first) first.focus();
    });
  });
  document.querySelectorAll("[data-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      var box = document.getElementById(button.getAttribute("data-close"));
      if (box) box.hidden = true;
    });
  });
})();
