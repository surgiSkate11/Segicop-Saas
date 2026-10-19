// Zona de arrastre del certificado: soltar un PDF equivale a elegirlo en el selector y enviar el formulario.
(function () {
  document.querySelectorAll("[data-zona-carga]").forEach(function (zona) {
    const formulario = zona.closest("form");
    const selector = zona.querySelector('input[type="file"]');
    if (!formulario || !selector) return;

    ["dragenter", "dragover"].forEach(function (evento) {
      zona.addEventListener(evento, function (e) {
        e.preventDefault();
        zona.classList.add("sc-zona-carga--activa");
      });
    });
    ["dragleave", "drop"].forEach(function (evento) {
      zona.addEventListener(evento, function () {
        zona.classList.remove("sc-zona-carga--activa");
      });
    });
    zona.addEventListener("drop", function (e) {
      e.preventDefault();
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      selector.files = e.dataTransfer.files;
      formulario.requestSubmit();
    });
    selector.addEventListener("change", function () {
      if (selector.files.length) formulario.requestSubmit();
    });
  });
})();
