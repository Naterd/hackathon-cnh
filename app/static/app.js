if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");

const photo = document.getElementById("photo");
const preview = document.getElementById("preview");
const status = document.getElementById("status");

photo?.addEventListener("change", () => {
  const file = photo.files[0];
  if (!file) return;
  if (preview.src) URL.revokeObjectURL(preview.src);
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  status.textContent = "Got your photo. Matching is coming soon.";
});
