const STATIC_MODE = location.hostname.endsWith("github.io") || location.protocol === "file:" || new URLSearchParams(location.search).has("static") || Boolean(window.JOB_SCOUT_STATIC);
const PAGE_SIZE = 50;
const state = {
  jobs: [], visible: PAGE_SIZE, tab: "all", loading: true,
  lastRefreshId: Number(localStorage.getItem("lastRefreshId") || 0),
  loadedDataRefreshId: 0,
  lastVisitAt: localStorage.getItem("jobScoutLastVisit") || "",
  initialLoad: true,
};
let snapshotPromise = null;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  document.querySelector('meta[name="theme-color"]').content = isDark ? "#0b1518" : "#102f3a";
  const button = $("#themeButton");
  button.setAttribute("aria-pressed", String(isDark));
  button.innerHTML = `<span class="button-icon">${isDark ? "☀" : "◐"}</span> ${isDark ? "Light mode" : "Dark mode"}`;
}

function sourceBadges(job) {
  const sources = Array.isArray(job.sources) && job.sources.length ? job.sources : ["Employer site"];
  const visible = sources.slice(0, 2).map(source => `<span class="source-badge">${escapeHtml(source)}</span>`).join("");
  const remainder = sources.length > 2 ? `<span class="source-more">+${sources.length - 2}</span>` : "";
  return `<div class="source-list" title="${escapeHtml(sources.join(", "))}">${visible}${remainder}</div>`;
}

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

async function getStaticMeta() {
  const response = await fetch(`data/meta.json?t=${Date.now()}`, {cache: "no-store"});
  if (!response.ok) throw new Error("The cloud refresh status is unavailable");
  return response.json();
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

function isNewSinceVisit(job) {
  if (!state.lastVisitAt || !job.first_seen) return false;
  const firstSeen = new Date(job.first_seen).getTime();
  const lastVisit = new Date(state.lastVisitAt).getTime();
  return Number.isFinite(firstSeen) && Number.isFinite(lastVisit) && firstSeen > lastVisit;
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
    if (search && !`${job.company} ${job.title} ${job.location} ${(job.sources || []).join(" ")}`.toLowerCase().includes(search)) return false;
    if (category !== "All" && job.category !== category) return false;
    if (gradOnly && !job.grad_2027) return false;
    if (days !== "all") {
      if (!job.posted_date) return false;
      const since = new Date(); since.setHours(0, 0, 0, 0); since.setDate(since.getDate() - Number(days));
      if (new Date(`${job.posted_date}T12:00:00`) < since) return false;
    }
    if (state.tab === "visa" && !(job.visa_status.startsWith("Yes") || job.visa_status.startsWith("Likely"))) return false;
    if (state.tab === "new" && !isNewSinceVisit(job)) return false;
    if (state.tab === "saved" && !job.saved) return false;
    if (state.tab === "applied" && job.status !== "Applied") return false;
    return true;
  });
}

function renderJobs() {
  const jobs = filteredJobs();
  const shown = jobs.slice(0, state.visible);
  $("#resultCount").textContent = jobs.length
    ? `Showing ${shown.length.toLocaleString()} of ${jobs.length.toLocaleString()} matching roles`
    : "0 matching roles";
  $("#emptyState").hidden = jobs.length !== 0;
  $(".table-wrap").hidden = jobs.length === 0;
  const remaining = Math.max(0, jobs.length - shown.length);
  const loadMore = $("#loadMore");
  loadMore.hidden = remaining === 0;
  loadMore.textContent = remaining
    ? `Show ${Math.min(PAGE_SIZE, remaining).toLocaleString()} more roles (${remaining.toLocaleString()} remaining)`
    : "All matching roles shown";
  const newCount = state.jobs.filter(isNewSinceVisit).length;
  $("#newTabCount").textContent = newCount ? ` (${newCount.toLocaleString()})` : "";
  $("#jobRows").innerHTML = shown.map(job => {
    const posted = prettyDate(job.posted_date);
    const isNew = isNewSinceVisit(job);
    const options = ["Not applied", "Applied", "Interviewing", "Offer", "Rejected"].map(value => `<option${job.status === value ? " selected" : ""}>${value}</option>`).join("");
    return `<tr data-id="${job.id}" class="${isNew ? "new-job" : ""}">
      <td class="company-role"><div class="company-line"><span class="company-avatar">${escapeHtml(initials(job.company))}</span><div><strong>${escapeHtml(job.company)}${isNew ? '<span class="new-badge">NEW</span>' : ""}</strong><small>${escapeHtml(job.title)}</small></div></div><span class="category-tag">${escapeHtml(job.category)}${job.grad_2027 ? " · 2027" : ""}</span></td>
      <td class="location-cell">${escapeHtml(job.location || "United States")}</td>
      <td class="source-cell">${sourceBadges(job)}</td>
      <td class="date-cell"><strong>${escapeHtml(posted.date)}</strong><small>${escapeHtml(posted.age)}</small></td>
      <td>${visaBadge(job)}</td>
      <td><select class="status-select" aria-label="Application status">${options}</select></td>
      <td class="row-actions"><button class="save-button${job.saved ? " saved" : ""}" aria-label="${job.saved ? "Unsave" : "Save"} job" title="Save job">${job.saved ? "★" : "☆"}</button><a class="apply-button" href="${escapeHtml(job.url)}" target="_blank" rel="noopener">Apply ↗</a></td>
    </tr>`;
  }).join("");
}

function showMoreJobs() {
  const jobs = filteredJobs();
  const firstNewIndex = Math.min(state.visible, jobs.length);
  if (firstNewIndex >= jobs.length) return;
  state.visible = Math.min(state.visible + PAGE_SIZE, jobs.length);
  renderJobs();
  const firstNewRow = $$("#jobRows tr")[firstNewIndex];
  if (firstNewRow) {
    firstNewRow.tabIndex = -1;
    firstNewRow.focus({preventScroll: true});
    requestAnimationFrame(() => firstNewRow.scrollIntoView({behavior: "smooth", block: "start"}));
  }
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
  jobs.sort((left, right) => Number(isNewSinceVisit(right)) - Number(isNewSinceVisit(left)));
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
  const results = latest?.source_results || {};
  const healthy = Object.values(results).filter(item => item.status === "ok").length;
  const degraded = Object.values(results).filter(item => item.status === "degraded").length;
  const failed = Object.keys(results).length - healthy - degraded;
  const hasSourceIssues = degraded > 0 || failed > 0;
  $("#lastRefresh").textContent = running ? "Checking sources now…" : refreshDescription(latest);
  $("#refreshState").textContent = running ? "Refreshing" : latest?.status === "failed" ? "Needs attention" : hasSourceIssues ? "Partially degraded" : "Up to date";
  $("#refreshState").className = `status-pill ${running ? "running" : latest?.status === "failed" ? "error" : hasSourceIssues ? "warning" : ""}`;
  $("#refreshButton").classList.toggle("refreshing", running);
  $("#refreshButton").disabled = running;
  const total = Object.keys(results).length;
  const issueText = [degraded ? `${degraded} degraded` : "", failed ? `${failed} unavailable` : ""].filter(Boolean).join(" · ");
  $("#sourceHealth").textContent = total ? `${healthy} of ${total} feed groups healthy${issueText ? ` · ${issueText}` : ""} · Excel updated automatically` : "Building your first job index…";
  if (latest?.id && latest.id > state.lastRefreshId && latest.status === "completed") {
    localStorage.setItem("lastRefreshId", latest.id);
    state.lastRefreshId = latest.id;
    if (!state.initialLoad && latest.discovered_count > 0) notifyNewJobs(latest.discovered_count);
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
    if (STATIC_MODE) {
      const meta = await getStaticMeta();
      if (meta.refresh?.id && meta.refresh.id !== state.loadedDataRefreshId) {
        await loadJobs(true);
        await loadStats();
      }
      return;
    }
    const wasRunning = await loadStats();
    if (wasRunning || state.loading) await loadJobs(false);
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

$("#themeButton").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("jobScoutTheme", theme);
  applyTheme(theme);
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
  state.tab = tab.dataset.tab; state.visible = PAGE_SIZE; renderJobs();
}));
["#searchInput", "#categoryFilter", "#daysFilter", "#gradFilter"].forEach(selector => {
  $(selector).addEventListener(selector === "#searchInput" ? "input" : "change", () => { state.visible = PAGE_SIZE; renderJobs(); });
});
$("#loadMore").addEventListener("click", showMoreJobs);
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
  if (!STATIC_MODE) {
    state.initialLoad = false;
    return;
  }
  if (state.lastVisitAt && "Notification" in window && Notification.permission === "granted") {
    const count = state.jobs.filter(isNewSinceVisit).length;
    if (count) notifyNewJobs(count);
  }
  localStorage.setItem("jobScoutLastVisit", new Date().toISOString());
  state.initialLoad = false;
}

if (STATIC_MODE) {
  $("#excelButton").href = "data/Job_Scout_New_Grad_2027.xlsx";
  $("#refreshButton").innerHTML = '<span class="refresh-icon">↻</span> Check latest';
}
applyTheme(document.documentElement.dataset.theme || "light");
if ("Notification" in window && Notification.permission === "granted") $("#alertButton").textContent = "● Alerts on";
loadJobs().then(loadStats).then(markVisitAndNotify).catch(error => showToast(`Could not load the app: ${error.message}`));
setInterval(poll, STATIC_MODE ? 300000 : 30000);
