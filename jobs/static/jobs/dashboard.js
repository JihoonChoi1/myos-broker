"use strict";
const $ = id => document.getElementById(id);
const labels = {queued: "Queued", running: "Running", success: "Success", failed: "Failed", crashed: "Crashed", timeout: "Timed out"};
let selected = null;
let allJobs = [];
let signature = "";
const date = value => value ? new Date(value).toLocaleString("en-US", {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";

async function request(url, options) {
  const response = await fetch(url, {cache: "no-store", ...options, signal: AbortSignal.timeout(10000)});
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error?.message || `Server error (${response.status}). Check the server and database connection.`);
  if (!body) throw new Error("The server did not return valid JSON.");
  return body;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderJobs() {
  const jobs = allJobs.filter(job => !$('filter').value || job.status === $('filter').value);
  const nextSignature = JSON.stringify([jobs, selected]);
  if (signature === nextSignature) return;
  signature = nextSignature;
  const rows = jobs.map(job => {
    const row = element("tr", undefined, job.id === selected ? "selected" : "");
    const file = element("td");
    const button = element("button", job.binary || "Unnamed file", "job-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(job.id === selected));
    button.prepend(element("span", `#${job.id}`, "job-id"));
    button.addEventListener("click", () => selectJob(job.id));
    file.append(button);
    const status = element("td");
    status.append(element("span", labels[job.status] || job.status, `badge ${job.status}`));
    row.append(file, status, element("td", job.slot ?? "—"), element("td", date(job.created_at)));
    return row;
  });
  $("jobs-body").replaceChildren(...rows);
  $("empty").hidden = jobs.length !== 0;
  const queued = allJobs.filter(j => j.status === "queued").length;
  const running = allJobs.filter(j => j.status === "running").length;
  $("summary").textContent = `${allJobs.length} total / ${queued} queued / ${running} running`;
}

async function loadDetail(id) {
  const job = await request(`/jobs/${id}`);
  if (selected !== id) return;
  $("log-heading").textContent = `#${id} serial log`;
  $("selected-status").textContent = `${labels[job.status] || job.status} / Exit code ${job.exit_code ?? "—"}`;
  $("detail").textContent = job.detail || (job.status === "queued" ? "Waiting for a worker to pick up this job." : "Running. Logs are saved when the job finishes.");
  $("serial").textContent = job.serial_log || "No saved logs yet.";
}

async function selectJob(id) {
  selected = id;
  renderJobs();
  $("log-heading").textContent = `#${id} serial log`;
  $("selected-status").textContent = "Loading…";
  $("detail").textContent = "Loading job results…";
  $("serial").textContent = "";
  try { await loadDetail(id); }
  catch (error) { if (selected === id) $("detail").textContent = error.message; }
}

async function refresh() {
  try {
    const [jobs, slots] = await Promise.all([request("/jobs"), request("/slots")]);
    allJobs = jobs.jobs;
    renderJobs();
    $("slots").replaceChildren(...slots.slots.map(slot => {
      const line = element("div", undefined, "slot");
      line.append(element("strong", `Slot ${slot.id}`), element("span", slot.status === "busy" ? `Running #${slot.current_job_id}` : "Available"));
      return line;
    }));
    if (!slots.slots.length) $("slots").textContent = "No slots found. Check the database migrations.";
    if (selected !== null) await loadDetail(selected);
    $("connection").textContent = "Status refreshes every 2 seconds / Serial logs appear after completion";
  } catch (error) {
    $("connection").textContent = `${error.message} Retrying shortly.`;
  }
}

$("filter").addEventListener("change", renderJobs);
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const file = $("binary").files[0];
  const message = $("upload-message");
  message.className = "";
  if (!file || file.size === 0 || file.size > Number(event.currentTarget.dataset.maxBytes)) {
    message.textContent = "Choose a nonempty file no larger than 24 KiB.";
    message.className = "error";
    return;
  }
  const data = new FormData();
  data.append("binary", file);
  const url = $("skip-validation").checked ? "/jobs?validate=false" : "/jobs";
  $("submit-button").disabled = true;
  message.textContent = "Uploading file…";
  try {
    const job = await request(url, {method: "POST", body: data});
    message.textContent = `Job #${job.id} added to the queue.`;
    $("upload-form").reset();
    $("filter").value = "";
    await refresh();
    await selectJob(job.id);
  } catch (error) {
    message.textContent = `${error.message} If the response was interrupted, check the run history before submitting again.`;
    message.className = "error";
  } finally { $("submit-button").disabled = false; }
});

async function poll() {
  if (!document.hidden) await refresh();
  window.setTimeout(poll, 2000);
}
poll();
