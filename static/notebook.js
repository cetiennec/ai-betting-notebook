// The only script in the notebook, and it does three small things that
// plain HTML cannot: the print button, copying a link to the clipboard,
// and hiding a "see more" that has nothing left to show. Kept in a file of its own rather than in onclick attributes
// so that the content security policy can stay at script-src 'self'.
document.addEventListener("DOMContentLoaded", function () {
  var button = document.getElementById("print-this");
  if (button) {
    button.addEventListener("click", function () { window.print(); });
  }

  // A reasoning is clamped to three lines by the stylesheet, but whether
  // three lines is a cut at all depends on how wide the page happens to
  // be. Where nothing is hidden, the control that would show the rest is
  // taken away: a button that does nothing is worse than no button.
  function tidyFolds() {
    var folds = document.querySelectorAll(".because.folded");
    Array.prototype.forEach.call(folds, function (box) {
      var toggle = box.querySelector(".more-toggle");
      var text = box.querySelector(".text");
      if (!toggle || !text || toggle.checked) { return; }
      box.classList.toggle("fits", text.scrollHeight <= text.clientHeight + 1);
    });
  }
  tidyFolds();
  var settling;
  window.addEventListener("resize", function () {
    window.clearTimeout(settling);
    settling = window.setTimeout(tidyFolds, 150);
  });

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
