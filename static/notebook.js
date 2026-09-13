// The only script in the notebook, and it does two small things that
// plain HTML cannot: the print button, and copying a link to the
// clipboard. Kept in a file of its own rather than in onclick attributes
// so that the content security policy can stay at script-src 'self'.
document.addEventListener("DOMContentLoaded", function () {
  var button = document.getElementById("print-this");
  if (button) {
    button.addEventListener("click", function () { window.print(); });
  }

  // Shown only once this has run and the clipboard is really there: a
  // button that copies nothing is worse than no button at all.
  var copiers = document.querySelectorAll("button.copy-link");
  if (!navigator.clipboard) { return; }
  Array.prototype.forEach.call(copiers, function (copier) {
    copier.hidden = false;
    copier.addEventListener("click", function () {
      navigator.clipboard.writeText(copier.getAttribute("data-link")).then(
        function () {
          var said = copier.textContent;
          copier.textContent = "Link copied";
          copier.disabled = true;
          window.setTimeout(function () {
            copier.textContent = said;
            copier.disabled = false;
          }, 2000);
        },
        function () { copier.textContent = "Copy failed — select the address instead"; }
      );
    });
  });
});
