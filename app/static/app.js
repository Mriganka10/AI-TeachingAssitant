const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
let email = "";

// Collections each assistant reads from.
const LESSON_COLLECTIONS = ["previous_notes", "books", "case_studies"];
const REVIEW_COLLECTIONS = ["research_papers"];
const COLLECTION_LABELS = {
  previous_notes: "Lecture notes",
  books: "Textbook or book chapter",
  case_studies: "Case study",
  research_papers: "Research paper · used in reviews",
  manuscripts: "Manuscript for review",
};
// Research downloads are told apart by file name: review report, clean revision, tracked changes.
const REVIEW_FILES = [
  { match: /-revised-tracked-changes\.docx$/, group: "students", label: "Revised paper, tracked changes", note: "Word, accept or reject each edit", order: 3 },
  { match: /-revised\.docx$/, group: "students", label: "Revised paper", note: "Word, clean copy to edit", order: 1 },
  { match: /-revised\.pdf$/, group: "students", label: "Revised paper", note: "PDF, clean copy", order: 2 },
  { match: /\.pdf$/, group: "teacher", label: "Review report", note: "PDF, ready to share", order: 1 },
  { match: /\.docx$/, group: "teacher", label: "Review report", note: "Word, easy to edit", order: 2 },
  { match: /\.json$/, group: "raw", label: "Review data", note: "JSON for other systems", order: 9 },
];
const DECISIONS = {
  accept: "Accept",
  minor_revision: "Minor revision",
  major_revision: "Major revision",
  reject_and_resubmit: "Reject and resubmit",
};
const CRITERIA = {
  originality: "Originality",
  significance: "Significance",
  methodological_rigor: "Methodological rigour",
  evidence_and_analysis: "Evidence and analysis",
  clarity_and_structure: "Clarity and structure",
  literature_coverage: "Literature coverage",
  reproducibility: "Reproducibility",
  ethics_and_reporting: "Ethics and reporting",
};
const FILES = {
  pdf: { group: "teacher", label: "Teacher's guide", note: "PDF, ready to print" },
  docx: { group: "teacher", label: "Teacher's guide", note: "Word, easy to edit" },
  pptx: { group: "students", label: "Student slides", note: "PowerPoint with diagrams and animations" },
  mp4: { group: "students", label: "Explainer video", note: "Narrated MP4" },
  json: { group: "raw", label: "Lesson data", note: "JSON for other systems" },
};
const STATUS = {
  running: { label: "Writing", tone: "working" },
  content_ready: { label: "Building files", tone: "working" },
  documents_ready: { label: "Recording video", tone: "working" },
  completed: { label: "Ready", tone: "ready" },
  failed: { label: "Failed", tone: "failed" },
  artifact_failed: { label: "Needs retry", tone: "failed" },
};
const TERMINAL = ["completed", "failed", "artifact_failed"];
const state = { documents: [], jobs: [], pollToken: 0 };

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  return parseApiResponse(response, url);
}

async function parseApiResponse(response, url = "request") {
  const contentType = response.headers.get("content-type") || "";
  const raw = await response.text();
  const isJson = contentType.includes("application/json");
  let payload = null;
  if (isJson && raw) {
    try {
      payload = JSON.parse(raw);
    } catch (_) {
      throw new Error("The server returned malformed data. Please try again.");
    }
  }
  if (!response.ok) {
    const detail = payload?.detail;
    if (typeof detail === "string") throw new Error(detail);
    if (Array.isArray(detail) && detail[0]?.msg) throw new Error(detail[0].msg);
    if (payload?.message) throw new Error(payload.message);
    throw new Error(nonJsonApiMessage(response, raw, url));
  }
  if (!isJson) throw new Error(nonJsonApiMessage(response, raw, url));
  return payload ?? {};
}

function nonJsonApiMessage(response, raw, url) {
  const preview = String(raw || "").trim().slice(0, 120);
  if (response.status === 401 || preview.includes("<!DOCTYPE")) {
    return "Your session has ended. Please refresh the page and sign in again.";
  }
  if ([502, 503, 504].includes(response.status)) {
    return "The service is busy or briefly unavailable. Please try again in a minute.";
  }
  return `Unexpected server response for ${url}. Please refresh and try again.`;
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
}

function formatSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Sign in
// ---------------------------------------------------------------------------

function setAuthMessage(message = "", isError = false) {
  $("#auth-message").textContent = message;
  $("#auth-message").classList.toggle("error", isError);
}

function showLoginView(message = "") {
  history.replaceState(null, "", "/login");
  $("#auth-eyebrow").textContent = "SECURE FACULTY ACCESS";
  $("#auth-title").textContent = "Welcome back";
  $("#auth-intro").textContent = "Sign in with your institutional email. We'll send you a one-time code, so there is no password to remember.";
  $("#email-register").classList.add("hidden");
  $("#otp-verify").classList.add("hidden");
  $("#otp-request").classList.remove("hidden");
  setAuthMessage(message);
  $("#email").focus();
}

function showRegisterView(message = "") {
  history.replaceState(null, "", "/register");
  $("#auth-eyebrow").textContent = "FIRST-TIME VERIFICATION";
  $("#auth-title").textContent = "Verify your email";
  $("#auth-intro").textContent = "Enter your email once. We'll send a verification link before sign-in codes are enabled.";
  $("#otp-request").classList.add("hidden");
  $("#otp-verify").classList.add("hidden");
  $("#email-register").classList.remove("hidden");
  setAuthMessage(message);
  const emailValue = $("#email").value || email;
  if (emailValue) $("#register-email-input").value = emailValue;
  $("#register-email-input").focus();
}

$("#otp-request").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setAuthMessage("Sending your code...");
  try {
    email = $("#email").value;
    const data = await api("/api/auth/request-otp", { method: "POST", body: JSON.stringify({ email }) });
    $("#otp-request").classList.add("hidden");
    $("#otp-verify").classList.remove("hidden");
    $("#otp-destination").textContent = `Sent to ${data.email}`;
    setAuthMessage(data.dev_otp ? `Development code: ${data.dev_otp}` : data.message);
    $("#otp").focus();
  } catch (error) {
    setAuthMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#open-register").onclick = (event) => {
  event.preventDefault();
  showRegisterView();
};

$("#back-to-login").onclick = (event) => {
  event.preventDefault();
  showLoginView();
};

$("#email-register").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setAuthMessage("Requesting your verification link...");
  try {
    email = $("#register-email-input").value;
    const data = await api("/api/auth/register-email", { method: "POST", body: JSON.stringify({ email }) });
    setAuthMessage(data.message);
  } catch (error) {
    setAuthMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#change-email").onclick = () => {
  $("#otp-verify").classList.add("hidden");
  $("#otp-request").classList.remove("hidden");
  $("#otp").value = "";
  setAuthMessage();
  $("#email").focus();
};

$("#otp-verify").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setAuthMessage("Checking your code...");
  try {
    const user = await api("/api/auth/verify", {
      method: "POST",
      body: JSON.stringify({ email, otp: $("#otp").value }),
    });
    history.replaceState(null, "", "/#/home");
    showWorkspace(user);
  } catch (error) {
    setAuthMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#logout").onclick = async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.href = "/login";
};

// ---------------------------------------------------------------------------
// Workspace shell and routing (#/home, #/create, #/lessons, #/materials, #/lesson/<id>)
// ---------------------------------------------------------------------------

const PAGE_TITLES = {
  home: "Home",
  create: "New lesson",
  lessons: "My lessons",
  review: "Review a paper",
  reviews: "Paper reviews",
  materials: "Uploaded files",
  lesson: "Details",
};

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function showWorkspace(user) {
  const displayName = user.email.split("@")[0].replace(/[._-]+/g, " ");
  const titleName = displayName.replace(/\b\w/g, (letter) => letter.toUpperCase());
  const generic = titleName.toLowerCase() === "professor";
  $("#login").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#user-name").textContent = generic ? "Professor" : `Prof. ${titleName}`;
  $("#user-email").textContent = user.email;
  $("#user-avatar").textContent = titleName.charAt(0) || "P";
  $("#welcome").textContent = generic ? `${greeting()}, Professor.` : `${greeting()}, Professor ${titleName}.`;
  $("#today-label").textContent = new Date()
    .toLocaleDateString("en", { weekday: "long", day: "numeric", month: "long" })
    .toUpperCase();
  route();
}

function go(page) {
  const target = page.startsWith("#") ? page : `#/${page}`;
  if (location.hash === target) route();
  else location.hash = target;
}

function route() {
  const [, page = "home", id] = (location.hash || "#/home").split("/");
  const name = PAGE_TITLES[page] ? page : "home";
  state.pollToken += 1; // stop polling any lesson we navigated away from
  $$(".page").forEach((section) => section.classList.toggle("hidden", section.dataset.page !== name));
  $$(".nav-item[data-go]").forEach((item) => {
    const active = item.dataset.go === name || (name === "lesson" && item.dataset.go === "lessons");
    item.classList.toggle("active", active);
  });
  $("#crumb").textContent = PAGE_TITLES[name];
  $(".sidebar").classList.remove("open");
  window.scrollTo({ top: 0 });

  if (name === "home") refreshHome();
  if (name === "lessons") refreshLessons();
  if (name === "materials") refreshMaterials();
  if (name === "create") refreshCreate();
  if (name === "review") refreshReview();
  if (name === "reviews") refreshReviews();
  if (name === "lesson" && id) openLesson(id);
}

window.addEventListener("hashchange", route);
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-go]");
  if (!target) return;
  event.preventDefault();
  go(target.dataset.go);
});
$("#mobile-menu").onclick = () => $(".sidebar").classList.toggle("open");

async function loadDocuments() {
  state.documents = await api("/api/documents");
  return state.documents;
}

async function loadJobs() {
  state.jobs = await api("/api/jobs");
  return state.jobs;
}

// ---------------------------------------------------------------------------
// Home and lesson lists
// ---------------------------------------------------------------------------

const isReview = (job) => job.agent_type === "research";

function lessonRow(job) {
  const status = STATUS[job.status] || { label: "Queued", tone: "working" };
  const context = [
    isReview(job) ? "Paper review" : "",
    job.course && job.course !== "General" ? job.course : "",
    formatDate(job.started_at),
  ].filter(Boolean).join(" · ");
  const icon = isReview(job) ? "✎" : (job.title || "L").charAt(0).toUpperCase();
  return `<button class="lesson-row" data-go="#/lesson/${escapeHtml(job.id)}">
    <span class="row-icon ${isReview(job) ? "review" : ""}">${escapeHtml(icon)}</span>
    <span><strong>${escapeHtml(job.title)}</strong><small>${escapeHtml(context)}</small></span>
    <span class="pill ${status.tone}">${escapeHtml(status.label)}</span>
    <span class="chev">›</span>
  </button>`;
}

function emptyLessons() {
  return `<div class="empty">
    <strong>No lessons yet</strong>
    <span>Your lessons will appear here, ready to download again at any time.</span>
    <button class="secondary-button" data-go="create">+ Create your first lesson</button>
  </div>`;
}

function emptyReviews() {
  return `<div class="empty">
    <strong>No paper reviews yet</strong>
    <span>Upload a manuscript to get a reviewer's report and a revised version of the paper.</span>
    <button class="secondary-button" data-go="review">+ Review a paper</button>
  </div>`;
}

async function refreshHome() {
  try {
    const [documents, jobs] = await Promise.all([loadDocuments(), loadJobs()]);
    const ready = jobs.filter((job) => job.status === "completed").length;
    const lessons = jobs.filter((job) => !isReview(job));
    $("#stat-lessons").textContent = lessons.length;
    $("#stat-reviews").textContent = jobs.length - lessons.length;
    $("#stat-ready").textContent = ready;
    $("#stat-documents").textContent = documents.length;
    $("#recent-lessons").innerHTML = jobs.length ? jobs.slice(0, 5).map(lessonRow).join("") : emptyLessons();
    const hasMaterials = documents.some((doc) => LESSON_COLLECTIONS.includes(doc.collection));
    $("#setup-materials").classList.toggle("done", hasMaterials);
    $("#setup-lesson").classList.toggle("done", lessons.length > 0);
    $$(".setup-list li.done .tick").forEach((tick) => { tick.textContent = "✓"; });
    $("#setup-score").textContent = `${1 + Number(hasMaterials) + Number(lessons.length > 0)} of 3`;
  } catch (error) {
    $("#recent-lessons").innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

async function refreshLessons() {
  try {
    const jobs = (await loadJobs()).filter((job) => !isReview(job));
    $("#lesson-list").innerHTML = jobs.length ? jobs.map(lessonRow).join("") : emptyLessons();
  } catch (error) {
    $("#lesson-list").innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

async function refreshReviews() {
  try {
    const jobs = (await loadJobs()).filter(isReview);
    $("#review-list").innerHTML = jobs.length ? jobs.map(lessonRow).join("") : emptyReviews();
  } catch (error) {
    $("#review-list").innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Course materials
// ---------------------------------------------------------------------------

function documentRow(doc) {
  const extension = (doc.filename.split(".").pop() || "").toUpperCase().slice(0, 4);
  const used = [...LESSON_COLLECTIONS, ...REVIEW_COLLECTIONS, "manuscripts"].includes(doc.collection);
  const details = [formatSize(doc.size_bytes), doc.uploaded_at ? formatDate(doc.uploaded_at) : ""]
    .filter(Boolean)
    .join(" · ");
  return `<div class="doc-row ${used ? "" : "unused"}">
    <span class="row-icon">${escapeHtml(extension)}</span>
    <span><strong>${escapeHtml(doc.filename)}</strong><small>${escapeHtml(details)}</small></span>
    <span class="kind">${escapeHtml(COLLECTION_LABELS[doc.collection] || doc.collection.replaceAll("_", " "))}</span>
  </div>`;
}

async function refreshMaterials() {
  try {
    const documents = await loadDocuments();
    $("#materials-count").textContent = documents.length ? `${documents.length} total` : "";
    $("#documents").innerHTML = documents.length
      ? documents.map(documentRow).join("")
      : `<div class="empty"><strong>Nothing uploaded yet</strong><span>Add lecture notes, a textbook chapter or a case study. Lessons will use them first.</span></div>`;
  } catch (error) {
    $("#documents").innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

function setUploadMessage(message = "", isError = false) {
  $("#upload-message").textContent = message;
  $("#upload-message").classList.toggle("error", isError);
}

$("#file").onchange = () => {
  const name = $("#file").files[0]?.name;
  $("#file-label").textContent = name || "Choose a file or drag it here";
  setUploadMessage();
};

["dragenter", "dragover"].forEach((type) => {
  $("#drop-zone").addEventListener(type, (event) => {
    event.preventDefault();
    $("#drop-zone").classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((type) => {
  $("#drop-zone").addEventListener(type, () => $("#drop-zone").classList.remove("dragging"));
});
$("#drop-zone").addEventListener("drop", (event) => {
  event.preventDefault();
  if (event.dataTransfer?.files?.length) {
    $("#file").files = event.dataTransfer.files;
    $("#file").onchange();
  }
});

$("#upload-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const file = $("#file").files[0];
  if (!file) {
    setUploadMessage("Choose a file first.", true);
    return;
  }
  const data = new FormData();
  data.append("collection", $("#collection").value);
  data.append("file", file);
  button.disabled = true;
  setUploadMessage(`Uploading ${file.name}...`);
  try {
    const response = await fetch("/api/documents", { method: "POST", body: data });
    await parseApiResponse(response, "/api/documents");
    event.target.reset();
    $("#file-label").textContent = "Choose a file or drag it here";
    setUploadMessage(`${file.name} was added.`);
    await refreshMaterials();
  } catch (error) {
    setUploadMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
};

// ---------------------------------------------------------------------------
// Create a lesson
// ---------------------------------------------------------------------------

async function refreshCreate() {
  syncVideoControls();
  try {
    const documents = await loadDocuments();
    const usable = documents.filter((doc) => LESSON_COLLECTIONS.includes(doc.collection)).length;
    $("#materials-note").innerHTML = usable
      ? `Using <strong>${usable} course material${usable === 1 ? "" : "s"}</strong> from your library.`
      : `No course materials yet. <button class="text-button" data-go="materials">Add some</button> so lessons match how you teach.`;
  } catch (_) {
    $("#materials-note").textContent = "";
  }
  $("#teach-topic").focus();
}

function syncVideoControls() {
  const enabled = $("#teach-video").checked;
  $("#video-duration-settings").classList.toggle("hidden", !enabled);
  $("#video-duration-settings").setAttribute("aria-hidden", String(!enabled));
  $("#teach-video").setAttribute("aria-expanded", String(enabled));
  $("#teach-video-min").disabled = !enabled;
  $("#teach-video-max").disabled = !enabled;
  $("#video-output").classList.toggle("muted-out", !enabled);
  $("#video-output-note").textContent = enabled
    ? `Between ${$("#teach-video-min").value} and ${$("#teach-video-max").value} minutes, narrated.`
    : "Optional. Turn it on in step 3.";
}

function validateVideoRange() {
  const minimum = Number($("#teach-video-min").value);
  const maximum = Number($("#teach-video-max").value);
  const invalid = minimum > maximum;
  $("#teach-video-max").setCustomValidity(invalid ? "The longest length must be at least the shortest." : "");
  syncVideoControls();
  return !invalid;
}

function syncQuickPicks() {
  const minutes = $("#teach-duration").value;
  $$(".quick-picks button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.minutes === minutes);
  });
}

$$(".quick-picks button").forEach((button) => {
  button.onclick = () => {
    $("#teach-duration").value = button.dataset.minutes;
    syncQuickPicks();
  };
});
$("#teach-duration").addEventListener("input", syncQuickPicks);
$("#teach-video").addEventListener("change", syncVideoControls);
$("#teach-video-min").addEventListener("input", validateVideoRange);
$("#teach-video-max").addEventListener("input", validateVideoRange);

$("#teaching-form").onsubmit = async (event) => {
  event.preventDefault();
  const videoRequested = $("#teach-video").checked;
  if (videoRequested && !validateVideoRange()) {
    $("#teach-video-max").reportValidity();
    return;
  }
  const button = $("#submit-lesson");
  button.disabled = true;
  button.querySelector("span").textContent = "Starting...";
  try {
    const queued = await api("/api/agents/teaching", {
      method: "POST",
      body: JSON.stringify({
        topic: $("#teach-topic").value.trim(),
        course: $("#teach-course").value.trim() || "General",
        audience: $("#teach-audience").value.trim() || "University students",
        duration_minutes: +$("#teach-duration").value,
        difficulty: $('input[name="difficulty"]:checked').value,
        instructions: $("#teach-instructions").value.trim(),
        use_web_search: $("#teach-web").checked,
        generate_video: videoRequested,
        ...(videoRequested
          ? { video_min_minutes: +$("#teach-video-min").value, video_max_minutes: +$("#teach-video-max").value }
          : {}),
        collections: LESSON_COLLECTIONS,
      }),
    });
    event.target.reset();
    syncQuickPicks();
    syncVideoControls();
    go(`#/lesson/${queued.job_id}`);
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Create lesson";
  }
};

// ---------------------------------------------------------------------------
// Review a paper
// ---------------------------------------------------------------------------

function manuscriptSource() {
  return $('input[name="manuscript-source"]:checked').value;
}

function syncManuscriptSource() {
  const existing = manuscriptSource() === "existing";
  $("#manuscript-drop").classList.toggle("hidden", existing);
  $("#manuscript-existing-field").classList.toggle("hidden", !existing);
}

async function refreshReview() {
  syncManuscriptSource();
  try {
    const documents = await loadDocuments();
    const manuscripts = documents.filter((doc) => doc.collection === "manuscripts");
    const others = documents.filter((doc) => doc.collection !== "manuscripts");
    const option = (doc) => `<option value="${escapeHtml(doc.id)}">${escapeHtml(doc.filename)}</option>`;
    $("#manuscript-existing").innerHTML = documents.length
      ? `${manuscripts.length ? `<optgroup label="Manuscripts">${manuscripts.map(option).join("")}</optgroup>` : ""}
         ${others.length ? `<optgroup label="Other uploads">${others.map(option).join("")}</optgroup>` : ""}`
      : '<option value="">No uploaded files yet</option>';
    const papers = documents.filter((doc) => REVIEW_COLLECTIONS.includes(doc.collection)).length;
    $("#review-note").innerHTML = papers
      ? `Compares with <strong>${papers} research paper${papers === 1 ? "" : "s"}</strong> from your library.`
      : `No research papers to compare with yet. <button class="text-button" data-go="materials">Add some</button> for a stronger literature check.`;
  } catch (_) {
    $("#review-note").textContent = "";
  }
}

$$('input[name="manuscript-source"]').forEach((input) => input.addEventListener("change", syncManuscriptSource));
$("#manuscript-file").onchange = () => {
  $("#manuscript-label").textContent = $("#manuscript-file").files[0]?.name || "Choose the paper or drag it here";
};
["dragenter", "dragover"].forEach((type) => {
  $("#manuscript-drop").addEventListener(type, (event) => {
    event.preventDefault();
    $("#manuscript-drop").classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((type) => {
  $("#manuscript-drop").addEventListener(type, () => $("#manuscript-drop").classList.remove("dragging"));
});
$("#manuscript-drop").addEventListener("drop", (event) => {
  event.preventDefault();
  if (event.dataTransfer?.files?.length) {
    $("#manuscript-file").files = event.dataTransfer.files;
    $("#manuscript-file").onchange();
  }
});
$("#review-revision").addEventListener("change", () => {
  $("#revision-output").classList.toggle("muted-out", !$("#review-revision").checked);
});

async function uploadManuscript(file) {
  const data = new FormData();
  data.append("collection", "manuscripts");
  data.append("file", file);
  const response = await fetch("/api/documents", { method: "POST", body: data });
  return parseApiResponse(response, "/api/documents");
}

$("#review-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = $("#submit-review");
  const label = button.querySelector("span");
  let documentId = "";
  if (manuscriptSource() === "upload") {
    const file = $("#manuscript-file").files[0];
    if (!file) {
      alert("Choose the paper to review first.");
      return;
    }
    button.disabled = true;
    label.textContent = "Uploading...";
    try {
      documentId = (await uploadManuscript(file)).id;
    } catch (error) {
      alert(error.message);
      button.disabled = false;
      label.textContent = "Review paper";
      return;
    }
  } else {
    documentId = $("#manuscript-existing").value;
    if (!documentId) {
      alert("Upload the paper first, or choose an uploaded file.");
      return;
    }
  }
  button.disabled = true;
  label.textContent = "Starting...";
  try {
    const queued = await api("/api/agents/research", {
      method: "POST",
      body: JSON.stringify({
        manuscript_document_id: documentId,
        discipline: $("#review-discipline").value.trim(),
        paper_type: $("#review-type").value,
        target_venue: $("#review-venue").value.trim(),
        author_name: $("#review-author").value.trim(),
        instructions: $("#review-instructions").value.trim(),
        generate_revision: $("#review-revision").checked,
        use_web_search: $("#review-web").checked,
        collections: $("#review-related").checked ? REVIEW_COLLECTIONS : [],
      }),
    });
    event.target.reset();
    $("#manuscript-label").textContent = "Choose the paper or drag it here";
    syncManuscriptSource();
    go(`#/lesson/${queued.job_id}`);
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    label.textContent = "Review paper";
  }
};

// ---------------------------------------------------------------------------
// One lesson: progress, downloads and a readable preview
// ---------------------------------------------------------------------------

async function openLesson(jobId) {
  const token = state.pollToken;
  resetLessonView();
  $("#loader").classList.remove("hidden");
  const startedAt = Date.now();
  try {
    for (;;) {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (token !== state.pollToken) return; // user navigated away
      renderLesson(job, Math.round((Date.now() - startedAt) / 1000));
      if (TERMINAL.includes(job.status)) break;
      await sleep(2500);
      if (token !== state.pollToken) return;
    }
  } catch (error) {
    if (token !== state.pollToken) return;
    showNotice(error.message, "error");
  } finally {
    if (token === state.pollToken) $("#loader").classList.add("hidden");
  }
}

function resetLessonView() {
  $("#lesson-title").textContent = "Loading lesson...";
  $("#lesson-meta").textContent = "";
  $("#lesson-message").classList.add("hidden");
  $("#downloads").classList.add("hidden");
  $("#lesson-preview").classList.add("hidden");
  $("#raw-data").classList.add("hidden");
  $$("#lesson-steps li").forEach((step) => { step.className = step.dataset.step === "video" ? "hidden" : ""; });
}

function showNotice(message, tone = "") {
  const notice = $("#lesson-message");
  notice.textContent = message;
  notice.className = `notice ${tone}`;
}

const STEP_LABELS = {
  teaching: { write: "Writing the lesson", files: "Building the files" },
  research: { write: "Reviewing the paper", files: "Revising and building files" },
};

function renderSteps(job, videoRequested, hasVideo) {
  const labels = STEP_LABELS[isReview(job) ? "research" : "teaching"];
  $('#lesson-steps li[data-step="write"] em').textContent = labels.write;
  $('#lesson-steps li[data-step="files"] em').textContent = labels.files;
  const order = ["write", "files", ...(videoRequested ? ["video"] : []), "done"];
  $('#lesson-steps li[data-step="video"]').classList.toggle("hidden", !videoRequested);
  const position = {
    running: 0,
    content_ready: 1,
    documents_ready: 2,
    completed: order.length,
    failed: 0,
    artifact_failed: 1,
  }[job.status] ?? 0;
  order.forEach((name, index) => {
    const step = $(`#lesson-steps li[data-step="${name}"]`);
    step.classList.remove("done", "active", "failed");
    if (["failed", "artifact_failed"].includes(job.status) && index === position) step.classList.add("failed");
    else if (index < position) step.classList.add("done");
    else if (index === position && !TERMINAL.includes(job.status)) step.classList.add("active");
  });
  if (job.status === "completed" && videoRequested && !hasVideo) {
    $('#lesson-steps li[data-step="video"]').classList.replace("done", "failed");
  }
}

function downloadCard(artifact) {
  const info = FILES[artifact.type] || { label: artifact.filename, note: "" };
  const note = [info.note, formatSize(artifact.size_bytes)].filter(Boolean).join(" · ");
  return `<a class="download" href="/api/artifacts/${escapeHtml(artifact.id)}" download>
    <span class="file-badge ${escapeHtml(artifact.type)}">${escapeHtml(artifact.type.toUpperCase())}</span>
    <span><strong>${escapeHtml(info.label)}</strong><small>${escapeHtml(note)}</small></span>
    <span class="get">Download ↓</span>
  </a>`;
}

function pendingCard(type) {
  const info = FILES[type];
  return `<div class="download pending">
    <span class="file-badge">${type.toUpperCase()}</span>
    <span><strong>${escapeHtml(info.label)}</strong><small>Being prepared...</small></span>
  </div>`;
}

function reviewDownloads(job) {
  const working = !TERMINAL.includes(job.status);
  const revisionRequested = (job.request || {}).generate_revision !== false;
  const cards = { teacher: [], students: [], raw: [] };
  const items = (job.artifacts || []).map((artifact) => {
    const info = REVIEW_FILES.find((entry) => entry.match.test(artifact.filename)) || REVIEW_FILES[3];
    return { artifact, info };
  }).sort((a, b) => a.info.order - b.info.order);
  for (const { artifact, info } of items) {
    const note = [info.note, formatSize(artifact.size_bytes)].filter(Boolean).join(" · ");
    cards[info.group].push(`<a class="download" href="/api/artifacts/${escapeHtml(artifact.id)}" download>
      <span class="file-badge ${escapeHtml(artifact.type)}">${escapeHtml(artifact.type.toUpperCase())}</span>
      <span><strong>${escapeHtml(info.label)}</strong><small>${escapeHtml(note)}</small></span>
      <span class="get">Download ↓</span>
    </a>`);
  }
  const pending = (label) => `<div class="download pending"><span class="file-badge">…</span>
    <span><strong>${escapeHtml(label)}</strong><small>Being prepared...</small></span></div>`;
  if (working && !cards.teacher.length) cards.teacher.push(pending("Review report"));
  if (working && revisionRequested && !cards.students.length) cards.students.push(pending("Revised paper"));
  $("#downloads-teacher-title").textContent = "Review report";
  $("#downloads-students-title").textContent = "Revised paper";
  $("#downloads").classList.toggle("hidden", !(cards.teacher.length || cards.students.length));
  $("#downloads-teacher").innerHTML = cards.teacher.join("") || '<p class="empty">No files yet.</p>';
  $("#downloads-students").innerHTML = cards.students.join("")
    || `<p class="empty">${revisionRequested ? "No files yet." : "Not requested for this review."}</p>`;
  $("#downloads-raw").innerHTML = cards.raw.join("");
}

function renderDownloads(job, videoRequested) {
  if (isReview(job)) {
    reviewDownloads(job);
    return;
  }
  $("#downloads-teacher-title").textContent = "For you";
  $("#downloads-students-title").textContent = "For your students";
  const artifacts = job.artifacts || [];
  const have = new Set(artifacts.map((artifact) => artifact.type));
  const expected = ["docx", "pdf", "pptx", ...(videoRequested ? ["mp4"] : [])];
  const working = !TERMINAL.includes(job.status);
  const cards = { teacher: [], students: [], raw: [] };
  for (const type of ["pdf", "docx", "pptx", "mp4", "json"]) {
    const group = FILES[type].group;
    artifacts.filter((artifact) => artifact.type === type).forEach((artifact) => cards[group].push(downloadCard(artifact)));
    if (working && expected.includes(type) && !have.has(type)) cards[group].push(pendingCard(type));
  }
  const showGroups = cards.teacher.length || cards.students.length;
  $("#downloads").classList.toggle("hidden", !showGroups);
  $("#downloads-teacher").innerHTML = cards.teacher.join("") || '<p class="empty">No files yet.</p>';
  $("#downloads-students").innerHTML = cards.students.join("") || '<p class="empty">No files yet.</p>';
  $("#downloads-raw").innerHTML = cards.raw.join("");
}

function listItems(values, limit = 99) {
  return (values || [])
    .slice(0, limit)
    .map((value) => `<li>${escapeHtml(typeof value === "object" ? value.objective || value.text || "" : value)}</li>`)
    .join("");
}

function renderReviewPreview(result) {
  const recommendation = result.recommendation || {};
  const scores = result.scorecard || [];
  const average = scores.length
    ? (scores.reduce((sum, item) => sum + Number(item.score || 0), 0) / scores.length).toFixed(1)
    : "";
  const bars = scores.map((item) => {
    const score = Number(item.score || 0);
    const tone = score <= 2 ? "low" : score === 3 ? "mid" : "";
    return `<li class="${tone}"><span>${escapeHtml(CRITERIA[item.criterion] || item.criterion)}</span>
      <span class="bar"><i style="width:${score * 20}%"></i></span><b>${score}/5</b></li>`;
  }).join("");
  const concerns = (result.major_concerns || []).slice(0, 4).map((item) => `<li>
      <strong><span class="tag ${escapeHtml(item.priority)}">${escapeHtml(item.priority)}</span>${escapeHtml(item.issue)}</strong>
      <small>${escapeHtml(item.suggested_fix || "")}</small></li>`).join("");
  const revisedSections = ((result.revision || {}).sections || [])
    .filter((section) => section.status === "revised" || section.status === "partly_revised").length;
  const counts = [
    [result.strengths?.length || 0, "Strengths"],
    [result.major_concerns?.length || 0, "Major concerns"],
    [result.minor_issues?.length || 0, "Minor issues"],
    [result.revision ? revisedSections : "-", "Sections revised"],
  ];
  $("#lesson-preview").innerHTML = `
    <div class="decision ${escapeHtml(recommendation.decision || "")}">
      <div><span class="overline">RECOMMENDATION</span>
        <strong>${escapeHtml(DECISIONS[recommendation.decision] || "Under review")}</strong>
        <p>${escapeHtml(recommendation.rationale || "")}</p></div>
      ${average ? `<div class="avg"><b>${average}</b><small>average of 5</small></div>` : ""}
    </div>
    <div class="preview-grid">
      <div class="panel"><h3>Scores</h3><ul class="scores">${bars || '<li class="empty">No scores.</li>'}</ul></div>
      <div class="panel"><h3>Main concerns</h3><ul class="concerns">${concerns || '<li class="empty">None.</li>'}</ul></div>
    </div>
    <div class="panel"><h3>At a glance</h3>
      <div class="counts">${counts.map(([value, label]) => `<div><strong>${value}</strong><small>${label}</small></div>`).join("")}</div>
    </div>`;
  $("#lesson-preview").classList.remove("hidden");
}

function renderPreview(result, job) {
  if (result && job && isReview(job)) {
    renderReviewPreview(result);
    return;
  }
  if (!result) {
    $("#lesson-preview").classList.add("hidden");
    return;
  }
  const overview = result.overview || {};
  const sections = result.lecture_sections || [];
  const parts = sections
    .map((section) => {
      const points = (section.content?.key_points || []).slice(0, 3);
      return `<div class="part">
        <div class="part-head"><strong>${escapeHtml(section.title)}</strong><span>${escapeHtml(section.minutes ?? "")} min</span></div>
        ${points.length ? `<ul>${listItems(points)}</ul>` : ""}
      </div>`;
    })
    .join("");
  const counts = [
    [result.mcqs?.length || 0, "Quiz questions"],
    [result.assignments?.length || 0, "Assignments"],
    [result.discussion_questions?.length || 0, "Discussion prompts"],
    [result.numerical_problems?.length || 0, "Practice problems"],
  ];
  $("#lesson-preview").innerHTML = `
    ${overview.lesson_purpose ? `<div class="panel"><h3>The lesson in one line</h3><p class="purpose">${escapeHtml(overview.lesson_purpose)}</p></div>` : ""}
    <div class="preview-grid">
      <div class="panel"><h3>Learning goals</h3><ol class="objective-list">${listItems(result.learning_objectives)}</ol></div>
      <div class="panel"><h3>Lesson plan</h3>${parts || '<p class="empty">No sections.</p>'}</div>
    </div>
    <div class="panel"><h3>Practice and assessment</h3>
      <div class="counts">${counts.map(([value, label]) => `<div><strong>${value}</strong><small>${label}</small></div>`).join("")}</div>
    </div>`;
  $("#lesson-preview").classList.remove("hidden");
}

function renderReview(job, elapsedSeconds) {
  const request = job.request || {};
  const result = job.result;
  $("#lesson-title").textContent = result?.title || request.manuscript_filename || "Your paper";
  $("#lesson-meta").textContent = [
    "Paper review",
    request.target_venue || "",
    request.discipline || "",
    job.started_at ? formatDate(job.started_at) : "",
  ].filter(Boolean).join(" · ");
  $("#lesson-kicker").textContent = (STATUS[job.status]?.label || "Queued").toUpperCase();
  $("#raw-data-title").textContent = "Advanced: review data (JSON)";
  renderSteps(job, false, false);
  renderDownloads(job, false);
  renderPreview(result, job);
  if (result) {
    $("#result").textContent = JSON.stringify(result, null, 2);
    $("#raw-data").classList.remove("hidden");
  }
  if (job.status === "failed") {
    showNotice(job.error || "This paper could not be reviewed. Please try again.", "error");
  } else if (job.status === "artifact_failed") {
    showNotice(`The review was written, but the files could not be built. ${job.error || ""}`, "error");
  } else if (job.status === "completed" && job.warning) {
    showNotice(job.warning, "warning");
  } else if (job.status === "completed") {
    showNotice("Your review is ready. Highlighted [Author: ...] notes in the revised paper mark details only you can add.");
  } else if (job.status === "content_ready") {
    showNotice(`The review is written. Now revising the paper and building the files... (${elapsedSeconds}s)`);
  } else {
    showNotice(`Reading and reviewing the paper... (${elapsedSeconds}s). You can leave this page; the review keeps going and will appear in Paper reviews.`);
  }
}

function syncSection(review) {
  const back = $('[data-page="lesson"] .back-link');
  back.dataset.go = review ? "reviews" : "lessons";
  back.textContent = review ? "← Paper reviews" : "← My lessons";
  $$(".nav-item[data-go]").forEach((item) => {
    item.classList.toggle("active", item.dataset.go === (review ? "reviews" : "lessons"));
  });
}

function renderLesson(job, elapsedSeconds) {
  syncSection(isReview(job));
  if (isReview(job)) {
    renderReview(job, elapsedSeconds);
    return;
  }
  $("#raw-data-title").textContent = "Advanced: lesson data (JSON)";
  const request = job.request || {};
  const result = job.result;
  const videoRequested = Boolean(request.generate_video);
  const hasVideo = (job.artifacts || []).some((artifact) => artifact.type === "mp4");

  $("#lesson-title").textContent = result?.title || request.topic || "Your lesson";
  const level = request.difficulty ? `${request.difficulty.charAt(0).toUpperCase()}${request.difficulty.slice(1)}` : "";
  $("#lesson-meta").textContent = [
    request.course && request.course !== "General" ? request.course : "",
    request.duration_minutes ? `${request.duration_minutes} minutes` : "",
    level,
    job.started_at ? formatDate(job.started_at) : "",
  ].filter(Boolean).join(" · ");
  $("#lesson-kicker").textContent = (STATUS[job.status]?.label || "Lesson").toUpperCase();

  renderSteps(job, videoRequested, hasVideo);
  renderDownloads(job, videoRequested);
  renderPreview(result);

  if (result) {
    $("#result").textContent = JSON.stringify(result, null, 2);
    $("#raw-data").classList.remove("hidden");
  }

  if (job.status === "failed") {
    showNotice(job.error || "This lesson could not be created. Please try again.", "error");
  } else if (job.status === "artifact_failed") {
    showNotice(`The lesson was written, but the files could not be built. ${job.error || ""}`, "error");
  } else if (job.status === "completed" && job.warning) {
    showNotice(job.warning, "warning");
  } else if (job.status === "completed") {
    showNotice("Your lesson is ready. Share the slides and video with students, and keep the guide for yourself.");
  } else if (job.status === "documents_ready") {
    showNotice("Your guide and slides are ready to download. The video is still being recorded.");
  } else {
    showNotice(`Working on it... (${elapsedSeconds}s). You can leave this page; the lesson keeps going and will appear in My lessons.`);
  }
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function boot() {
  try {
    showWorkspace(await api("/api/auth/me"));
  } catch (_) {
    if (location.pathname === "/register") showRegisterView();
    else if (location.search.includes("verified=1")) showLoginView("Email verified. You can request your sign-in code now.");
  }
}

syncQuickPicks();
syncVideoControls();
boot();