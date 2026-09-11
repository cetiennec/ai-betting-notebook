// The only script in the notebook, and it does one thing: the print
// button. Kept in a file of its own rather than an onclick so that the
// content security policy can stay at script-src 'self'.
document.addEventListener("DOMContentLoaded", function () {
  var button = document.getElementById("print-this");
  if (button) {
    button.addEventListener("click", function () { window.print(); });
  }
});
