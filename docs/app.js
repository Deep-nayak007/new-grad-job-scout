const STATIC_MODE = location.hostname.endsWith("github.io") || location.protocol === "file:" || new URLSearchParams(location.search).has("static") || Boolean(window.JOB_SCOUT_STATIC);
const state = { jobs: [], visible: 50, tab: "all", loading: true, lastRefreshId: Number(localStorage.getItem("lastRefreshId") || 0), loadedDataRefreshId: 0 };
let snapshotPromise = null;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function loadTracker() {
  try { return JSON.parse(localStorage.getItem("jobScoutTracker") || "{}"); } catch { return {}; }
}

function saveTracker(tracker) { localStorage.setItem("jobScoutTracker", JSON.stringify(tracker)); }

function getSnapshot(force = false) {
  if (force) snapshotPromise = null;
  if (!snapshotPromise) snapshotPromise = fetch(`data/jobs.json?t=${Date.now()}`, {cache: "no-store"}).then(response => {
    if (!response.ok) throw new Error("The cloud job snapshot is unavailable");
    return response.json();
  });
  return snapshotPromise;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3500);
}

function prettyDate(iso) {
  if (!iso) return { date: "Not listed", age: "" };
  const value = new Date(`${iso}T12:00:00`);
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const days = Math.max(0, Math.round((today - value) / 86400000));
  const label = days === 0 ? "Today" : days === 1 ? "Yesterday" : `${days} days ago`;
  return { date: value.toLocaleDateString(undefined, { month: "short", day: "numeric" }), age: label };
}

function initials(company) {
  return company.split(/\s+/).slice(0, 2).map(word => word[0]).join("").toUpperCase();
}

function visaBadge(job) {
  const value = job.visa_status || "Unknown";
  const css = value.startsWith("Yes") ? "yes" : value.startsWith("Likely") ? "likely" : value.startsWith("No") ? "no" : "unknown";
  const label = value.startsWith("Yes") ? "● Explicit" : value.startsWith("Likely") ? "◐ Likely" : value.startsWith("No") ? "× Restricted" : "— Unknown";
  return `<span class="visa-badge ${css}" title="${escapeHtml(job.visa_evidence)}">${label}</span>`;
}

function filteredJobs() {
  const search = $("#searchInput").value.trim().toLowerCase();
  const category = $("#categoryFilter").value;
  const days = $("#daysFilter").value;
  const gradOnly = $("#gradFilter").checked;
  return state.jobs.filter(job => {
    if (search && !`${job.company} ${job.title} ${job.location}`.toLowerCase().includes(search)) return false;
    if (category !== "All" && job.category !== category) return false;
    if (gradOnly && !job.grad_2027) return false;
    if (days !== "all") {
      if (!job.posted_date) return false;
      const since = new Date(); since.setHours(0, 0, 0, 0); since.setDate(since.getDate() - Number(days));
      if (new Date(`${job.posted_date}T12:00:00`) < since) return false;
    }
    if (state.tab === "visa" && !(job.visa_status.startsWith("Yes") || job.visa_status.startsWith("Likely"))) return false;
    if (state.tab === "saved" && !job.saved) return false;
    if (state.tab === "applied" && job.status !== "Applied") return false;
    return true;
  });
}

function renderJobs() {
  const jobs = filteredJobs();
  const shown = jobs.slice(0, state.visible);
  $("#resultCount").textContent = `${jobs.length.toLocaleString()} matching role${jobs.length === 1 ? "" : "s"}`;
  $("#emptyState").hidden = jobs.length !== 0;
  $(".table-wrap").hidden = jobs.length === 0;
  $("#loadMore").hidden = jobs.length <= state.visible;
  $("#jobRows").innerHTML = shown.map(job => {
    const posted = prettyDate(job.posted_date);
    const options = ["Not applied", "Applied", "Interviewing", "Offer", "Rejected"].map(value => `<option${job.status === value ? " selected" : ""}>${value}</option>`).join("");
    return `<tr data-id="${job.id}">
      <td class="company-role"><div class="company-line"><span class="company-avatar">${escapeHtml(initials(job.company))}</span><div><strong>${escapeHtml(job.company)}</strong><small>${escapeHtml(job.title)}</small></div></div><span class="category-tag">${escapeHtml(job.category)}${job.grad_2027 ? " · 2027" : ""}</span></td>
      <td class="location-cell">${escapeHtml(job.location || "United States")}</td>
      <td class="date-cell"><strong>${escapeHtml(posted.date)}</strong><small>${escapeHtml(posted.age)}</small></td>
      <td>${visaBadge(job)}</td>
      <td><select class="status-select" aria-label="Application status">${options}</select></td>
      <td class="row-actions"><button class="save-button${job.saved ? " saved" : ""}" aria-label="${job.saved ? "Unsave" : "Save"} job" title="Save job">${job.saved ? "★" : "☆"}</button><a class="apply-button" href="${escapeHtml(job.url)}" target="_blank" rel="noopener">Apply ↗</a></td>
    </tr>`;
  }).join("");
}

async function patchJob(id, changes) {
  if (STATIC_MODE) {
    const tracker = loadTracker();
    tracker[id] = {...(tracker[id] || {}), ...changes};
    saveTracker(tracker);
    return;
  }
  const response = await fetch(`/api/jobs/${id}`, { method: "PATCH", headers: {"Content-Type":"application/json"}, body: JSON.stringify(changes) });
  if (!response.ok) throw new Error("Could not save that change");
}

async function loadJobs(force = false) {
  let jobs;
  if (STATIC_MODE) {
    const snapshot = await getSnapshot(force);
    const tracker = loadTracker();
    jobs = snapshot.jobs.map(job => ({...job, status: tracker[job.id]?.status || "Not applied", saved: Boolean(tracker[job.id]?.saved)}));
  } else {
    const response = await fetch("/api/jobs?limit=5000");
    const data = await response.json();
    jobs = data.jobs;
  }
  state.jobs = jobs;
  state.loading = false;
  renderJobs();
}

function refreshDescription(refresh) {
  if (!refresh) return "No refresh has completed yet";
  const timestamp = refresh.finished_at || refresh.started_at;
  const value = new Date(timestamp);
  return `${value.toLocaleDateString(undefined, {month:"short", day:"numeric"})} at ${value.toLocaleTimeString(undefined, {hour:"numeric", minute:"2-digit"})}`;
}

async function loadStats() {
  let data;
  if (STATIC_MODE) {
    const snapshot = await getSnapshot();
    data = {stats: {...snapshot.stats}, refresh: {running: false, latest: snapshot.refresh}};
    data.stats.applied = state.jobs.filter(job => job.status === "Applied").length;
    data.stats.saved = state.jobs.filter(job => job.saved).length;
  } else {
    const response = await fetch("/api/stats");
    data = await response.json();
  }
  const stats = data.stats;
  $("#totalStat").textContent = stats.total.toLocaleString();
  $("#todayStat").textContent = stats.today.toLocaleString();
  $("#gradStat").textContent = stats.grad_2027.toLocaleString();
  $("#visaStat").textContent = stats.visa.toLocaleString();
  $("#appliedStat").textContent = stats.applied.toLocaleString();
  const latest = data.refresh.latest;
  const running = data.refresh.running;
  const dataChanged = Boolean(latest?.id && latest.status === "completed" && latest.id !== state.loadedDataRefreshId);
  if (latest?.id && latest.status === "completed") state.loadedDataRefreshId = latest.id;
  $("#lastRefresh").textContent = running ? "Checking sources now…" : refreshDescription(latest);
  $("#refreshState").textContent = running ? "Refreshing" : latest?.status === "failed" ? "Needs attention" : "Up to date";
  $("#refreshState").className = `status-pill ${running ? "running" : latest?.status === "failed" ? "error" : ""}`;
  $("#refreshButton").classList.toggle("refreshing", running);
  $("#refreshButton").disabled = running;
  const results = latest?.source_results || {};
  const healthy = Object.values(results).filter(item => item.status === "ok").length;
  const total = Object.keys(results).length;
  $("#sourceHealth").textContent = total ? `${healthy} of ${total} feed groups healthy · Excel updated automatically` : "Building your first job index…";
  if (latest?.id && latest.id > state.lastRefreshId && latest.status === "completed") {
    localStorage.setItem("lastRefreshId", latest.id);
    state.lastRefreshId = latest.id;
    if (latest.discovered_count > 0) notifyNewJobs(latest.discovered_count);
  }
  return running || dataChanged;
}

function notifyNewJobs(count) {
  const message = `${count} new matching job${count === 1 ? "" : "s"} found.`;
  showToast(message);
  if ("Notification" in window && Notification.permission === "granted") new Notification("Job Scout", { body: message, tag: "job-scout-new-jobs" });
}

async function poll() {
  try {
    const wasRunning = await loadStats();
    if (wasRunning || state.loading) await loadJobs(STATIC_MODE);
  } catch (error) { console.error(error); }
}

$("#refreshButton").addEventListener("click", async () => {
  $("#refreshButton").classList.add("refreshing");
  $("#refreshButton").disabled = true;
  if (STATIC_MODE) {
    try {
      await loadJobs(true); await loadStats();
      showToast("Checked the latest cloud snapshot.");
    } catch (error) { showToast(error.message); }
    $("#refreshButton").classList.remove("refreshing"); $("#refreshButton").disabled = false;
  } else {
    await fetch("/api/refresh", { method: "POST" });
    showToast("Refreshing all sources. This usually takes a few seconds.");
    setTimeout(poll, 800);
  }
});

$("#alertButton").addEventListener("click", async () => {
  if (!("Notification" in window)) return showToast("Browser notifications are not available here. macOS alerts still work while the app is running.");
  const permission = await Notification.requestPermission();
  $("#alertButton").textContent = permission === "granted" ? "● Alerts on" : "Alerts blocked";
  showToast(permission === "granted" ? "Alerts enabled. You’ll be notified after a refresh finds new roles." : "Notification permission was not granted.");
});

$$('.tab').forEach(tab => tab.addEventListener("click", () => {
  $$('.tab').forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  state.tab = tab.dataset.tab; state.visible = 50; renderJobs();
}));
["#searchInput", "#categoryFilter", "#daysFilter", "#gradFilter"].forEach(selector => {
  $(selector).addEventListener(selector === "#searchInput" ? "input" : "change", () => { state.visible = 50; renderJobs(); });
});
$("#loadMore").addEventListener("click", () => { state.visible += 50; renderJobs(); });
$("#jobRows").addEventListener("change", async event => {
  if (!event.target.matches(".status-select")) return;
  const id = event.target.closest("tr").dataset.id;
  const job = state.jobs.find(item => item.id === id);
  job.status = event.target.value;
  try { await patchJob(id, {status: job.status}); await loadStats(); showToast("Application status saved."); } catch (error) { showToast(error.message); }
});
$("#jobRows").addEventListener("click", async event => {
  const button = event.target.closest(".save-button");
  if (!button) return;
  const id = button.closest("tr").dataset.id;
  const job = state.jobs.find(item => item.id === id);
  job.saved = !job.saved;
  button.classList.toggle("saved", job.saved); button.textContent = job.saved ? "★" : "☆";
  try { await patchJob(id, {saved: job.saved}); await loadStats(); } catch (error) { showToast(error.message); }
});

async function markVisitAndNotify() {
  if (!STATIC_MODE) return;
  const previous = localStorage.getItem("jobScoutLastVisit");
  if (previous && "Notification" in window && Notification.permission === "granted") {
    const previousDate = previous.slice(0, 10);
    const count = state.jobs.filter(job => job.posted_date && job.posted_date > previousDate).length;
    if (count) notifyNewJobs(count);
  }
  localStorage.setItem("jobScoutLastVisit", new Date().toISOString());
}

if (STATIC_MODE) {
  $("#excelButton").href = "data/Job_Scout_New_Grad_2027.xlsx";
  $("#refreshButton").innerHTML = '<span class="refresh-icon">↻</span> Check latest';
}
if ("Notification" in window && Notification.permission === "granted") $("#alertButton").textContent = "● Alerts on";
loadJobs().then(loadStats).then(markVisitAndNotify).catch(error => showToast(`Could not load the app: ${error.message}`));
setInterval(poll, STATIC_MODE ? 300000 : 30000);
