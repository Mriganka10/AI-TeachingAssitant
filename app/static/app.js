const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
let email = "";

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) throw new Error((await response.json()).detail || "Request failed");
  return response.json();
}

function setAuthMessage(message = "", isError = false) {
  $("#auth-message").textContent = message;
  $("#auth-message").classList.toggle("error", isError);
}

function updateDate() {
  const now = new Date();
  $("#today-day").textContent = String(now.getDate()).padStart(2, "0");
  $("#today-month").textContent = now.toLocaleString("en", { month: "short" }).toUpperCase();
  $("#today-year").textContent = now.getFullYear();
}

function showWorkspace(user) {
  const displayName = user.email.split("@")[0].replace(/[._-]+/g, " ");
  const titleName = displayName.replace(/\b\w/g, (letter) => letter.toUpperCase());
  $("#login").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#user-name").textContent = titleName;
  $("#user-email").textContent = user.email;
  $("#user-avatar").textContent = titleName.charAt(0) || "P";
  $("#welcome").textContent = `Good morning, ${titleName}.`;
  updateDate();
  loadDocuments();
}

async function boot() {
  try { showWorkspace(await api("/api/auth/me")); } catch (_) {}
}

$("#otp-request").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setAuthMessage("Sending your secure code…");
  try {
    email = $("#email").value;
    const data = await api("/api/auth/request-otp", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
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
  setAuthMessage("Verifying your access…");
  try {
    const user = await api("/api/auth/verify", {
      method: "POST",
      body: JSON.stringify({ email, otp: $("#otp").value }),
    });
    showWorkspace(user);
  } catch (error) {
    setAuthMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#logout").onclick = async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
};

function openAgent(agent) {
  const isTeaching = agent === "teaching";
  $("#agent-workbench").classList.remove("hidden");
  $("#teaching-form").classList.toggle("hidden", !isTeaching);
  $("#research-form").classList.toggle("hidden", isTeaching);
  $("#workbench-kicker").textContent = isTeaching ? "TEACHING & CURRICULUM" : "RESEARCH & SYNTHESIS";
  $("#workbench-title").textContent = isTeaching ? "AI Teaching Assistant" : "Research Paper Assistant";
  $("#workbench-description").textContent = isTeaching
    ? "Tell the agent what you’re teaching and it will assemble a complete, review-ready package."
    : "Define the problem and the agent will synthesize your paper library into a research direction.";
  $$(".nav-item").forEach((item) => item.classList.remove("active"));
  document.querySelector(`.nav-item[data-agent-target="${agent}"]`)?.classList.add("active");
  $("#agent-workbench").scrollIntoView({ behavior: "smooth", block: "start" });
  $(".sidebar").classList.remove("open");
}

$$("[data-agent-target]").forEach((button) => {
  button.onclick = () => openAgent(button.dataset.agentTarget);
});

$("#close-workbench").onclick = () => {
  $("#agent-workbench").classList.add("hidden");
  $$(".nav-item").forEach((item) => item.classList.remove("active"));
  $('.nav-item[data-view="home"]').classList.add("active");
};

$('.nav-item[data-view="home"]').onclick = () => {
  $("#agent-workbench").classList.add("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
  $$(".nav-item").forEach((item) => item.classList.remove("active"));
  $('.nav-item[data-view="home"]').classList.add("active");
  $(".sidebar").classList.remove("open");
};

$('.nav-item[data-scroll-target="library"]').onclick = () => {
  $("#library").scrollIntoView({ behavior: "smooth", block: "center" });
  $(".sidebar").classList.remove("open");
};

$("#mobile-menu").onclick = () => $(".sidebar").classList.toggle("open");

async function loadDocuments() {
  const documents = await api("/api/documents");
  $("#documents").innerHTML = documents.length
    ? documents.map((doc) => `<span class="document-chip">${doc.collection.replaceAll("_", " ")} · ${doc.filename}</span>`).join("")
    : '<span class="empty-library">No documents uploaded yet.</span>';
}

$("#file").onchange = () => {
  const name = $("#file").files[0]?.name;
  if (name) $(".file-button span").textContent = name;
};

$("#upload-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const data = new FormData();
  data.append("collection", $("#collection").value);
  data.append("file", $("#file").files[0]);
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    const response = await fetch("/api/documents", { method: "POST", body: data });
    if (!response.ok) throw new Error((await response.json()).detail);
    event.target.reset();
    $(".file-button span").textContent = "Choose document";
    await loadDocuments();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Upload";
  }
};

async function run(url, payload) {
  $("#output").classList.remove("hidden");
  $("#loader").classList.remove("hidden");
  $("#result").textContent = "Your specialist agent is working…";
  $("#downloads").innerHTML = "";
  $("#output").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const data = await api(url, { method: "POST", body: JSON.stringify(payload) });
    $("#result").textContent = JSON.stringify(data.result, null, 2);
    $("#downloads").innerHTML = data.artifacts
      .map((artifact) => `<a class="download" href="/api/artifacts/${artifact.id}">↓ Download ${artifact.type.toUpperCase()}</a>`)
      .join("");
  } catch (error) {
    $("#result").textContent = error.message;
  } finally {
    $("#loader").classList.add("hidden");
  }
}

$("#teaching-form").onsubmit = (event) => {
  event.preventDefault();
  run("/api/agents/teaching", {
    topic: $("#teach-topic").value,
    course: $("#teach-course").value || "General",
    duration_minutes: +$("#teach-duration").value,
    use_web_search: $("#teach-web").checked,
    collections: ["previous_notes", "books", "case_studies"],
  });
};

$("#research-form").onsubmit = (event) => {
  event.preventDefault();
  run("/api/agents/research", {
    research_topic: $("#research-topic").value,
    discipline: $("#discipline").value || "General",
    use_web_search: $("#research-web").checked,
    collections: ["research_papers"],
  });
};

boot();
