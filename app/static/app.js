if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");

const $ = (id) => document.getElementById(id);

// Photos stay on this phone, in IndexedDB: {id, takenAt, blob, state: "queued" | "matched", talks, pick, degraded}.
// `talks` is the lookup's top 3 and `pick` the one the person chose ("Not it?" changes it). `degraded`:
// Elastic's inference service was busy, so the match skipped the image search.
const db = new Promise((resolve, reject) => {
  const open = indexedDB.open("slidekick", 1);
  open.onupgradeneeded = () => open.result.createObjectStore("photos", { keyPath: "id" });
  open.onsuccess = () => resolve(open.result);
  open.onerror = () => reject(open.error);
});

async function store(mode, fn) {
  const tx = (await db).transaction("photos", mode);
  const request = fn(tx.objectStore("photos"));
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve(request.result);
    tx.onerror = () => reject(tx.error);
  });
}
const allPhotos = async () => (await store("readonly", (s) => s.getAll())).sort((a, b) => a.takenAt - b.takenAt);
const getPhoto = (id) => store("readonly", (s) => s.get(id));
const putPhoto = (photo) => store("readwrite", (s) => s.put(photo));
const deletePhoto = (id) => store("readwrite", (s) => s.delete(id));

const urls = new Map();
const photoURL = (photo) => urls.get(photo.id) ?? urls.set(photo.id, URL.createObjectURL(photo.blob)).get(photo.id);

// Views. The hash is the route: #capture, #roll, #timeline, #match/<photo id>. Scanning has no route.
let tab = "capture";
let navigated = false;

function show(name) {
  for (const view of ["capture", "scan", "match", "roll", "timeline"]) $(view).hidden = view !== name;
  document.querySelector('meta[name="theme-color"]').content =
    { capture: "#151618", scan: "#07191d" }[name] ?? "#f2f8f9";
  if (["capture", "roll", "timeline"].includes(name)) tab = name;
  for (const link of document.querySelectorAll(".tabbar a")) {
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (name === "capture") startCamera();
  else stopCamera();
}

async function route() {
  const [name, id] = location.hash.slice(1).split("/");
  if (name === "match" && id) return openMatch(decodeURIComponent(id));
  if (name === "roll" || name === "timeline") {
    show(name);
    $(name).scrollTop = 0;
    return name === "roll" ? renderRoll() : renderTimeline();
  }
  show("capture");
  renderRollThumb();
}

addEventListener("hashchange", () => {
  navigated = true;
  route();
});

// Camera. It runs only while Capture is on screen.
const video = $("viewfinder");
let stream = null;
let starting = null;

function startCamera(ask = false) {
  starting ??= openCamera(ask).finally(() => (starting = null));
  return starting;
}

async function openCamera(ask) {
  if (stream) return;
  if (!navigator.mediaDevices?.getUserMedia) return cameraOff("unsupported");
  const state = await navigator.permissions?.query({ name: "camera" }).then((p) => p.state, () => "prompt");
  if (state !== "granted" && !ask) return cameraOff(state ?? "prompt");
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1440 } },
      audio: false,
    });
  } catch {
    return cameraOff("denied");
  }
  if ($("capture").hidden || document.hidden) return stopCamera(); // left while the prompt was up
  video.srcObject = stream;
  await video.play().catch(() => {});
  $("cam-off").hidden = true;
  $("aim").hidden = false;
  $("live").hidden = false;
  $("shutter").disabled = false;
}

function cameraOff(state) {
  $("cam-off").hidden = false;
  $("aim").hidden = true;
  $("live").hidden = true;
  $("shutter").disabled = true;
  $("allow-camera").hidden = state !== "prompt";
  $("cam-off-text").textContent = {
    denied: "Camera access is off for this site. Turn it on in the browser's site settings, or choose a photo instead.",
    unsupported: "This browser can't open the camera here. Choose a photo instead.",
  }[state] ?? "SlideKick uses the camera only while this screen is open. Only the photo you take is sent, to find the talk.";
}

function stopCamera() {
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  $("live").hidden = true;
  $("shutter").disabled = true;
}

$("allow-camera").onclick = () => startCamera(true);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopCamera();
  else if (!$("capture").hidden) startCamera();
});

$("shutter").onclick = async () => {
  if (!video.videoWidth) return;
  const flash = $("flash");
  flash.classList.remove("go");
  void flash.offsetWidth; // restart the animation
  flash.classList.add("go");
  const blob = await draw(video, video.videoWidth, video.videoHeight);
  setTimeout(() => handle(blob, Date.now()), 250); // let the flash show before Scanning covers it
};

$("photo").onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const takenAt = (await exifTime(file)) ?? file.lastModified ?? Date.now();
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return toast("That file isn't a photo SlideKick can read.");
  }
  const blob = await draw(bitmap, bitmap.width, bitmap.height);
  bitmap.close();
  handle(blob, takenAt);
};

// At most 2560 px on the long side, about 1 MB. The server cuts the slide out of the photo, and from
// the back of a room the slide is a fraction of it: at 1280 px a projected slide is about 400 px wide.
function draw(source, width, height) {
  const scale = Math.min(1, 2560 / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

// When the camera took the photo, from EXIF DateTimeOriginal. Read it before drawing to a canvas,
// which drops EXIF. Without OffsetTimeOriginal it's Mountain Time, KubeCon's zone (MST in November).
async function exifTime(file) {
  try {
    const view = new DataView(await file.slice(0, 256 * 1024).arrayBuffer());
    if (view.getUint16(0) !== 0xffd8) return null;
    for (let i = 2; i + 10 < view.byteLength; i += 2 + view.getUint16(i + 2)) {
      const marker = view.getUint16(i);
      if (marker === 0xffe1 && view.getUint32(i + 4) === 0x45786966) return tiffTime(view, i + 10); // "Exif"
      if (marker === 0xffda || marker >> 8 !== 0xff) return null;
    }
  } catch {
    // Not a JPEG we can read: fall back to the file's time.
  }
  return null;
}

function tiffTime(view, tiff) {
  const little = view.getUint16(tiff) === 0x4949;
  const u16 = (at) => view.getUint16(tiff + at, little);
  const u32 = (at) => view.getUint32(tiff + at, little);
  const find = (ifd, tag) => {
    for (let k = 0, n = u16(ifd); k < n; k++) if (u16(ifd + 2 + k * 12) === tag) return ifd + 2 + k * 12;
    return null;
  };
  const text = (entry) => {
    const length = u32(entry + 4);
    const at = length > 4 ? u32(entry + 8) : entry + 8;
    let s = "";
    for (let k = 0; k < length - 1; k++) s += String.fromCharCode(view.getUint8(tiff + at + k));
    return s;
  };
  const pointer = find(u32(4), 0x8769);
  if (pointer === null) return null;
  const exif = u32(pointer + 8);
  const original = find(exif, 0x9003);
  if (original === null) return null;
  const offset = find(exif, 0x9011);
  const iso = text(original).replace(/^(\d{4}):(\d\d):(\d\d) /, "$1-$2-$3T");
  const time = Date.parse(iso + (offset === null ? "-07:00" : text(offset)));
  return Number.isNaN(time) ? null : time;
}

// Photo ids with a lookup running, so a retry never races a capture over the same photo.
const inFlight = new Set();

// Save first, so a failed lookup still leaves the photo on the roll to retry.
async function handle(blob, takenAt) {
  const photo = { id: `${takenAt}-${Math.random().toString(36).slice(2, 8)}`, takenAt, blob, state: "queued", talks: [], pick: 0 };
  inFlight.add(photo.id); // before it's saved, where a retry could see it
  await putPhoto(photo);
  $("scan-photo").src = photoURL(photo);
  show("scan");
  say("Finding the talk…");
  const outcome = await lookup(photo);
  if ($("scan").hidden) return; // they moved on; the roll has the result
  if (outcome === "matched") {
    location.hash = `#match/${photo.id}`;
  } else {
    show("capture");
    renderRollThumb();
    toast(outcome);
  }
}

async function lookup(photo) {
  inFlight.add(photo.id);
  try {
    let response;
    try {
      response = await fetch("/api/lookup", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: photo.blob });
    } catch {
      return "Saved to your roll. It'll be matched when you're back online.";
    }
    // Only the photo's own fault removes it. Anything else may pass, so it stays queued.
    if (response.status === 400 || response.status === 413) {
      await deletePhoto(photo.id);
      return "Couldn't read that photo. Try another.";
    }
    if (response.status === 429) return "Saved to your roll. That's a lot of photos at once: tap Retry on the roll in a minute.";
    if (!response.ok) return "Saved to your roll. Search isn't answering, so it'll try again later.";
    const { talks, degraded } = await response.json();
    if (!talks.length) {
      await deletePhoto(photo.id);
      return "No talk matched that photo. Try again closer to the slide.";
    }
    Object.assign(photo, { state: "matched", talks, pick: 0, degraded });
    await putPhoto(photo);
    return "matched";
  } finally {
    inFlight.delete(photo.id);
  }
}

let retrying = false;
async function retryQueued() {
  if (retrying) return;
  retrying = true;
  $("retry").disabled = true;
  $("retry").textContent = "Matching…";
  try {
    for (const { id } of await allPhotos()) {
      // Re-read: since the list was read, a capture may have matched it (and a pick changed) or removed it.
      const photo = inFlight.has(id) ? null : await getPhoto(id);
      if (photo?.state === "queued" && !inFlight.has(id)) await lookup(photo);
    }
  } finally {
    retrying = false;
    $("retry").disabled = false;
    $("retry").textContent = "Retry";
  }
  if (!$("roll").hidden) renderRoll();
  if (!$("capture").hidden) renderRollThumb();
}
addEventListener("online", retryQueued);
$("retry").onclick = retryQueued;

// Match
let current = null;
const talkInfo = new Map();

function fetchTalk(id) {
  if (!talkInfo.has(id)) {
    const info = fetch(`/api/talk/${id}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    talkInfo.set(id, info);
    info.then((found) => found || talkInfo.delete(id)); // retry a failed fetch next time
  }
  return talkInfo.get(id);
}

async function openMatch(id) {
  const photo = await getPhoto(id);
  if (photo?.state !== "matched") return location.replace("#roll");
  current = photo;
  show("match");
  $("match").scrollTop = 0;
  renderMatch(photo);
}

function renderMatch(photo) {
  const talk = photo.talks[photo.pick];
  const moment = talk.moments[0];
  const at = clock(moment.start_sec);

  const hero = $("hero-slide").parentElement;
  const slide = $("hero-slide");
  hero.classList.remove("has-slide");
  $("hero-photo").src = photoURL(photo);
  slide.onload = () => hero.classList.add("has-slide");
  slide.src = `/api/slide/${moment.id}`;
  slide.alt = `Slide ${moment.n + 1} of the talk, as it appears in the recording`;
  if (slide.complete && slide.naturalWidth) slide.onload();

  $("match-tag").textContent = `Matched · slide ${moment.n + 1}`;
  $("match-meta").textContent = `KubeCon NA 2024 · your photo ${time(photo.takenAt)}`;
  $("match-title").textContent = talk.title;
  $("presenter").hidden = !talk.speakers;
  $("initials").textContent = talk.speakers ? initials(talk.speakers) : "";
  $("speakers").textContent = talk.speakers;
  $("recording").href = moment.url;
  $("recording").target = "_blank";
  $("recording-title").textContent = `Jump to ${at} in the recording`;
  $("recording-sub").textContent = "When this slide was on screen";
  $("match-note").hidden = !photo.degraded;

  const others = photo.talks.map((t, i) => [t, i]).filter(([, i]) => i !== photo.pick);
  $("others").hidden = !others.length;
  $("others-list").replaceChildren(...others.map(([other, i]) => {
    const button = el("button");
    button.type = "button";
    button.append(el("strong", "", other.title), el("span", "", other.speakers || "Speakers not listed"));
    button.onclick = async () => {
      photo.pick = i;
      await putPhoto(photo);
      $("match").scrollTop = 0;
      renderMatch(photo);
    };
    const item = el("li");
    item.append(button);
    return item;
  }));

  $("strip-head").hidden = true;
  $("strip").replaceChildren();
  fetchTalk(talk.talk_id).then((info) => {
    if (!info || current !== photo || photo.talks[photo.pick] !== talk) return;
    $("match-tag").textContent = `Matched · slide ${moment.n + 1} of ${info.slides.length}`;
    if (info.duration_sec) $("recording-sub").textContent = `When this slide was on screen · ${clock(info.duration_sec)} total`;
    renderStrip(info.slides, moment);
  });

  say(`Found it: ${talk.title}${talk.speakers ? `, by ${talk.speakers}` : ""}. The slide appears at ${at}.`);
}

// Eight slides around the matched one: each image is a full still from Elasticsearch, about 60 KB.
function renderStrip(slides, moment) {
  const strip = $("strip");
  const start = Math.max(0, Math.min(slides.findIndex((s) => s.id === moment.id) - 3, slides.length - 8));
  strip.replaceChildren(...slides.slice(start, start + 8).map((s) => {
    const link = el("a");
    link.href = s.url;
    link.target = "_blank";
    link.setAttribute("aria-label", `Slide ${s.n + 1}, at ${clock(s.start_sec)} in the recording`);
    const img = el("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = `/api/slide/${s.id}`;
    link.append(img, el("span", "", `${s.n + 1} · ${clock(s.start_sec)}`));
    const item = el("li", s.id === moment.id ? "here" : "");
    item.append(link);
    return item;
  }));
  $("strip-head").hidden = false;
  $("strip-count").textContent = `${slides.length} slides`;
  const here = strip.querySelector(".here");
  if (here) strip.scrollLeft = here.offsetLeft - (strip.clientWidth - here.offsetWidth) / 2;
}

$("back").onclick = () => (navigated ? history.back() : (location.hash = `#${tab}`));

$("share").onclick = async () => {
  const talk = current.talks[current.pick];
  const moment = talk.moments[0];
  const text = `${talk.title}${talk.speakers ? `, by ${talk.speakers}` : ""}, at ${clock(moment.start_sec)}`;
  if (navigator.share) return navigator.share({ title: talk.title, text, url: moment.url }).catch(() => {});
  await navigator.clipboard?.writeText(moment.url);
  toast("Link copied");
};

// Roll
async function renderRoll() {
  const photos = await allPhotos();
  const queued = photos.filter((p) => p.state === "queued").length;
  $("roll-sub").textContent = photos.length
    ? `${plural(photos.length, "slide")} so far · every talk's recording is on YouTube`
    : "No photos yet. Take one from Capture.";
  $("queue").hidden = !queued;
  $("queue-text").textContent = `${plural(queued, "photo")} ${queued === 1 ? "is" : "are"} waiting to match. Saved on this phone.`;
  $("roll-days").replaceChildren(...byDay(photos, (p) => p.takenAt).map(([day, list]) => {
    const head = el("div", "day-head");
    head.append(el("h2", "", day), el("span", "", plural(list.length, "slide")));
    const grid = el("ul", "grid");
    grid.append(...list.map((photo) => {
      const matched = photo.state === "matched";
      const tile = el(matched ? "a" : "div", matched ? "tile" : "tile queued");
      const img = el("img");
      img.src = photoURL(photo);
      img.alt = matched ? "" : `Photo from ${time(photo.takenAt)}, waiting to match`;
      tile.append(img, el("time", "", time(photo.takenAt)));
      if (matched) {
        tile.href = `#match/${photo.id}`;
        tile.setAttribute("aria-label", `${time(photo.takenAt)}: ${photo.talks[photo.pick].title}`);
      } else {
        tile.append(el("span", "badge", "Queued"));
      }
      const item = el("li");
      item.append(tile);
      return item;
    }));
    const section = el("section");
    section.append(head, grid);
    return section;
  }));
}

async function renderRollThumb() {
  const photos = await allPhotos();
  const thumb = $("roll-thumb");
  const img = thumb.querySelector("img");
  if (photos.length) img.src = photoURL(photos.at(-1));
  else img.removeAttribute("src");
  thumb.querySelector("span").textContent = photos.length || "";
  thumb.setAttribute("aria-label", `Photo roll, ${plural(photos.length, "photo")}`);
}

// Timeline: each talk you photographed, at the time of your first photo of it. The 2024 schedule
// slot and room aren't in the index yet.
async function renderTimeline() {
  const photos = (await allPhotos()).filter((p) => p.state === "matched");
  const talks = new Map();
  for (const photo of photos) {
    const talk = photo.talks[photo.pick];
    const entry = talks.get(talk.talk_id) ?? { talk, first: photo, count: 0 };
    talks.set(talk.talk_id, Object.assign(entry, { last: photo, count: entry.count + 1 }));
  }
  const entries = [...talks.values()];
  $("timeline-sub").textContent = entries.length
    ? `${plural(entries.length, "talk")} · ${plural(photos.length, "slide")} · every recording posted`
    : "Talks you photograph show up here, in the order you saw them.";
  $("timeline-days").replaceChildren(...byDay(entries, (e) => e.first.takenAt).map(([day, list]) => {
    const head = el("div", "tl-day");
    head.append(el("b", "", String(new Date(list[0].first.takenAt).getDate())), el("h2", "", day));
    const rows = el("ol", "tl");
    rows.append(...list.map((entry) => {
      const card = el("a", "card bracketed");
      card.href = `#match/${entry.last.id}`;
      const top = el("span", "card-top");
      top.append(el("span", "kicker", "KubeCon NA 2024"), el("span", "tag tint", "Recording"));
      const ticks = el("span", "ticks");
      ticks.setAttribute("aria-hidden", "true");
      ticks.append(...Array.from({ length: Math.min(entry.count, 8) }, () => el("i")));
      const foot = el("span", "card-foot");
      foot.append(el("span", "who", entry.talk.speakers), ticks);
      card.append(top, el("strong", "", entry.talk.title), foot, el("span", "visually-hidden", `, ${plural(entry.count, "photo")}`));
      const row = el("li");
      row.append(el("time", "", time(entry.first.takenAt)), el("span", "node"), card);
      return row;
    }));
    const section = el("section");
    section.append(head, rows);
    return section;
  }));
}

// Helpers
function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function byDay(items, when) {
  const days = new Map();
  for (const item of items) {
    const day = new Date(when(item)).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" }).replace(", ", " · ");
    days.set(day, [...(days.get(day) ?? []), item]);
  }
  return [...days];
}

const time = (ms) => new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function initials(speakers) {
  const words = speakers.split(/,|&| and /)[0].trim().split(/\s+/);
  return (words[0][0] + (words.length > 1 ? words.at(-1)[0] : "")).toUpperCase();
}

// 549.5 -> "9:09", 3723 -> "1:02:03"
function clock(sec) {
  const s = Math.floor(sec);
  return new Date(s * 1000).toISOString().slice(s >= 3600 ? 11 : 14, 19).replace(/^0/, "");
}

function say(text) {
  $("status").textContent = text;
}

let toastTimer;
function toast(text) {
  const box = $("toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), 4500);
}

route();
retryQueued();
