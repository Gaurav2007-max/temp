/* Minimal UI helpers */
document.addEventListener("DOMContentLoaded", () => {
  // Auto-dismiss flash alerts after a few seconds
  document.querySelectorAll(".alert-dismissible").forEach((el) => {
    setTimeout(() => {
      const btn = el.querySelector(".btn-close");
      if (btn) btn.click();
    }, 6000);
  });
});
