"use strict";

const form = document.querySelector("#url-form");
const input = document.querySelector("#url-input");
const submitButton = document.querySelector("#submit-button");
const pasteButton = document.querySelector("#paste-button");
const exampleButton = document.querySelector("#example-link");
const formError = document.querySelector("#form-error");
const statusPanel = document.querySelector("#status-panel");
const statusTitle = document.querySelector("#status-title");
const statusSource = document.querySelector("#status-source");
const statusPercent = document.querySelector("#status-percent");
const progressTrack = document.querySelector("#progress-track");
const progressFill = document.querySelector("#progress-fill");
const stageRow = document.querySelector("#stage-row");
const statusError = document.querySelector("#status-error");
const statusErrorMessage = document.querySelector("#status-error-message");
const tryAgainButton = document.querySelector("#try-again-button");
const results = document.querySelector("#results");
const resultTitle = document.querySelector("#result-title");
const expiryNote = document.querySelector("#expiry-note");
const videoGrid = document.querySelector("#video-grid");
const startOverButton = document.querySelector("#start-over-button");

const exampleUrl = "https://www.universityutahfootandankle.med.utah.edu/anatomy-of-foot-ankle-foot-ankle-pain-specialists-south-jordan-ut/";
const stageOrder = ["extracting", "downloading", "processing", "ready"];

let pollTimer = null;
let currentJobId = null;
let pollFailures = 0;

function normalizeUrl(value) {
  const trimmed = value.trim();
  if (trimmed && !/^https?:\/\//i.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

function setSubmitting(submitting) {
  submitButton.disabled = submitting;
  submitButton.querySelector("span").textContent = submitting
    ? "Starting extraction"
    : "Find the video";
}

function showFormError(message) {
  formError.textContent = message || "";
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 MB";
  }
  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1
  );
  const value = bytes / Math.pow(1024, unitIndex);
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function sourceLabel(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname}${parsed.pathname}`;
  } catch {
    return url;
  }
}

function updateStages(stage) {
  const currentIndex = stageOrder.indexOf(stage);
  stageRow.querySelectorAll("[data-stage]").forEach((element) => {
    const index = stageOrder.indexOf(element.dataset.stage);
    element.classList.toggle("complete", currentIndex > index);
    element.classList.toggle("active", currentIndex === index);
  });
}

function renderStatus(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  statusPanel.hidden = false;
  statusTitle.textContent = job.message || "Preparing your video";
  statusSource.textContent = sourceLabel(job.source_url || input.value);
  statusPercent.textContent = `${progress}%`;
  progressFill.style.width = `${progress}%`;
  progressTrack.setAttribute("aria-valuenow", String(progress));
  updateStages(job.stage);

  if (job.status === "failed") {
    statusError.hidden = false;
    statusErrorMessage.textContent = job.error || "The video could not be prepared.";
    setSubmitting(false);
  } else {
    statusError.hidden = true;
  }
}

function renderResults(job) {
  clearTimeout(pollTimer);
  setSubmitting(false);
  resultTitle.textContent = job.title || "Your video";
  expiryNote.textContent = "Available for about 1 hour";
  videoGrid.replaceChildren();

  job.files.forEach((file) => {
    const card = document.createElement("article");
    card.className = "video-card";

    const frame = document.createElement("div");
    frame.className = "video-frame";

    const video = document.createElement("video");
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.src = file.play_url;
    frame.append(video);

    const meta = document.createElement("div");
    meta.className = "video-meta";

    const description = document.createElement("div");
    description.style.minWidth = "0";

    const name = document.createElement("p");
    name.className = "video-name";
    name.textContent = file.name;

    const size = document.createElement("p");
    size.className = "video-size";
    const extension = file.name.includes(".")
      ? file.name.split(".").pop().toUpperCase()
      : "VIDEO";
    size.textContent = `${formatBytes(file.size)} / ${extension} video`;

    const download = document.createElement("a");
    download.className = "download-button";
    download.href = file.download_url;
    download.textContent = `Download ${extension}`;

    description.append(name, size);
    meta.append(description, download);
    card.append(frame, meta);
    videoGrid.append(card);
  });

  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.error || `Request failed with HTTP ${response.status}.`;
  } catch {
    return `Request failed with HTTP ${response.status}.`;
  }
}

async function pollJob() {
  if (!currentJobId) {
    return;
  }

  try {
    const response = await fetch(`/api/jobs/${currentJobId}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const job = await response.json();
    pollFailures = 0;
    renderStatus(job);

    if (job.status === "ready") {
      renderResults(job);
      return;
    }
    if (job.status === "failed") {
      return;
    }

    pollTimer = window.setTimeout(pollJob, 1100);
  } catch (error) {
    pollFailures += 1;
    if (pollFailures < 4) {
      pollTimer = window.setTimeout(pollJob, 1800);
      return;
    }
    renderStatus({
      source_url: input.value,
      status: "failed",
      stage: "failed",
      progress: Number(progressTrack.getAttribute("aria-valuenow")) || 0,
      message: "Connection to the server was lost",
      error: error.message,
    });
  }
}

async function startJob(url) {
  clearTimeout(pollTimer);
  currentJobId = null;
  pollFailures = 0;
  showFormError("");
  statusError.hidden = true;
  results.hidden = true;
  setSubmitting(true);

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ url }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const job = await response.json();
    currentJobId = job.id;
    renderStatus(job);
    statusPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    pollTimer = window.setTimeout(pollJob, 500);
  } catch (error) {
    setSubmitting(false);
    showFormError(error.message);
    input.focus();
  }
}

function resetExperience() {
  clearTimeout(pollTimer);
  currentJobId = null;
  pollFailures = 0;
  statusPanel.hidden = true;
  statusError.hidden = true;
  results.hidden = true;
  videoGrid.replaceChildren();
  showFormError("");
  setSubmitting(false);
  input.value = "";
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const url = normalizeUrl(input.value);
  input.value = url;
  if (!url) {
    showFormError("Paste a page URL first.");
    input.focus();
    return;
  }
  startJob(url);
});

pasteButton.addEventListener("click", async () => {
  showFormError("");
  if (!navigator.clipboard || !navigator.clipboard.readText) {
    input.focus();
    showFormError("Use your browser's paste command in the URL field.");
    return;
  }
  try {
    input.value = await navigator.clipboard.readText();
    input.focus();
  } catch {
    input.focus();
    showFormError("Clipboard access was blocked. Paste into the field manually.");
  }
});

exampleButton.addEventListener("click", () => {
  input.value = exampleUrl;
  form.requestSubmit();
});

tryAgainButton.addEventListener("click", resetExperience);
startOverButton.addEventListener("click", resetExperience);
