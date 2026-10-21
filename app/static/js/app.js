// Every page: a plain notice when a button that updates part of the page does not work.
(function () {
  function show(text) {
    var box = document.getElementById("request-error");
    if (!box) return;
    box.textContent = text;
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }
  document.addEventListener("htmx:responseError", function (event) {
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (status === 401 || status === 403) {
      show("You have been logged out. Reload the page and log in again.");
    } else if (status === 404) {
      show("That is no longer here. It may have been deleted. Reload the page to see the latest.");
    } else {
      show("That did not work: something went wrong on the server. Nothing was saved. Try again, or reload the page.");
    }
  });
  document.addEventListener("htmx:sendError", function () {
    show("Order Desk could not be reached. Check it is still running, then try again.");
  });
  // No inline handlers, so the pages run under a strict Content-Security-Policy.
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) window.print();
  });
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt;
    if (event.detail.successful && form && form.matches && form.matches("form[data-reset-after-success]")) form.reset();
  });
  document.addEventListener("htmx:beforeRequest", function () {
    var box = document.getElementById("request-error");
    if (box) box.hidden = true;
  });
})();
