if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");

const photo = document.getElementById("photo");
const label = document.querySelector("label[for=photo]");
const preview = document.getElementById("preview");
const status = document.getElementById("status");
const result = document.getElementById("result");

photo?.addEventListener("change", async () => {
  const file = photo.files[0];
  if (!file) return;
  if (preview.src) URL.revokeObjectURL(preview.src);
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  result.hidden = true;
  photo.disabled = true;
  status.classList.remove("visually-hidden");
  status.textContent = "Finding the talk…";

  try {
    const res = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: await shrink(file),
    });
    if (!res.ok) throw new Error(`lookup returned HTTP ${res.status}`);
    const { talks } = await res.json();
    if (talks.length) show(talks[0]);
    else status.textContent = "No talk matched. Try again closer to the screen, with the whole slide in frame.";
  } catch (err) {
    console.error(err);
    status.textContent = "Couldn't look up that photo. Check your connection and try again.";
  } finally {
    photo.disabled = false;
    photo.value = "";
    label.textContent = "Take another photo";
  }
});

// Phone photos are 3–12 MB, slow on venue wifi. The lookup shrinks to 1280 px anyway.
async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

function show(talk) {
  const moment = talk.moments[0];
  const at = clock(moment.start_sec);
  document.getElementById("result-title").textContent = talk.title;
  document.getElementById("result-speakers").textContent = talk.speakers;
  const link = document.getElementById("result-link");
  link.href = moment.url;
  link.textContent = `Watch from ${at}`;
  result.hidden = false;
  // The card shows the same thing; keep the sentence for screen readers only.
  status.classList.add("visually-hidden");
  const by = talk.speakers ? `, by ${talk.speakers}` : "";
  status.textContent = `Found it: ${talk.title}${by}. The slide appears at ${at}.`;
}

// 549.5 -> "9:09", 3723 -> "1:02:03"
function clock(sec) {
  const s = Math.floor(sec);
  return new Date(s * 1000).toISOString().slice(s >= 3600 ? 11 : 14, 19).replace(/^0/, "");
}
