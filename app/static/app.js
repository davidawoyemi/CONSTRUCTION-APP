const state = {
  projectId: null,
  estimateId: null,
};

function log(message) {
  const el = document.getElementById("log");
  const time = new Date().toLocaleTimeString();
  el.textContent = `[${time}] ${message}\n` + el.textContent;
}

function toMoney(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value || 0);
}

function setProjectMeta() {
  const meta = document.getElementById("project-meta");
  meta.textContent = state.projectId ? `Project ID: ${state.projectId}` : "No project selected.";
}

function setEstimateMeta(locked = false) {
  const meta = document.getElementById("estimate-meta");
  if (!state.estimateId) {
    meta.textContent = "No estimate selected.";
    return;
  }
  meta.textContent = `Estimate ID: ${state.estimateId}${locked ? " (locked)" : ""}`;
}

function renderEstimate(data) {
  const tbody = document.getElementById("line-items");
  tbody.innerHTML = "";
  let total = 0;

  data.line_items.forEach((item) => {
    total += item.subtotal_cost;
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.takeoff_item_id}</td>
      <td>${toMoney(item.unit_cost_selected)}</td>
      <td>${toMoney(item.subtotal_cost)}</td>
      <td>${item.freshness_status}</td>
      <td>${item.confidence_score.toFixed(2)}</td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("estimate-total").textContent = toMoney(total);
  setEstimateMeta(data.is_locked);
}

async function apiRequest(path, method = "GET", payload = null) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : null,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const message = error.detail || `Request failed (${response.status})`;
    throw new Error(message);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

document.getElementById("project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      name: document.getElementById("project-name").value,
      project_type: document.getElementById("project-type").value,
      zip_code: document.getElementById("project-zip").value,
    };
    const data = await apiRequest("/projects", "POST", payload);
    state.projectId = data.id;
    state.estimateId = null;
    setProjectMeta();
    setEstimateMeta();
    log(`Created project "${data.name}" (id=${data.id})`);
  } catch (err) {
    log(`Project error: ${err.message}`);
  }
});

document.getElementById("takeoff-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectId) {
    log("Create a project first.");
    return;
  }
  try {
    const payload = {
      csi_code: document.getElementById("takeoff-csi").value,
      item_name: document.getElementById("takeoff-name").value,
      quantity: Number(document.getElementById("takeoff-qty").value),
      unit: document.getElementById("takeoff-unit").value,
      waste_factor_pct: Number(document.getElementById("takeoff-waste").value),
    };
    const data = await apiRequest(`/projects/${state.projectId}/takeoff-items`, "POST", payload);
    log(`Added takeoff item "${data.item_name}" (id=${data.id})`);
  } catch (err) {
    log(`Takeoff error: ${err.message}`);
  }
});

document.getElementById("quote-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectId) {
    log("Create a project first.");
    return;
  }
  try {
    const payload = [
      {
        supplier_name: document.getElementById("quote-supplier").value,
        quote_ref: document.getElementById("quote-ref").value || null,
        csi_code: document.getElementById("quote-csi").value,
        item_name: document.getElementById("quote-name").value,
        unit: document.getElementById("quote-unit").value,
        quoted_unit_cost: Number(document.getElementById("quote-cost").value),
      },
    ];
    const data = await apiRequest(`/projects/${state.projectId}/supplier-quotes/import`, "POST", payload);
    log(`Imported ${data.length} supplier quote(s).`);
  } catch (err) {
    log(`Quote error: ${err.message}`);
  }
});

document.getElementById("estimate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectId) {
    log("Create a project first.");
    return;
  }
  try {
    const payload = { version_name: document.getElementById("estimate-name").value };
    const data = await apiRequest(`/projects/${state.projectId}/estimate-versions`, "POST", payload);
    state.estimateId = data.id;
    setEstimateMeta(data.is_locked);
    log(`Created estimate version "${data.version_name}" (id=${data.id})`);
  } catch (err) {
    log(`Estimate creation error: ${err.message}`);
  }
});

document.getElementById("reprice-btn").addEventListener("click", async () => {
  if (!state.estimateId) {
    log("Create an estimate version first.");
    return;
  }
  try {
    const data = await apiRequest(`/estimate-versions/${state.estimateId}/reprice`, "POST");
    renderEstimate(data);
    log("Estimate repriced successfully.");
  } catch (err) {
    log(`Reprice error: ${err.message}`);
  }
});

document.getElementById("lock-btn").addEventListener("click", async () => {
  if (!state.estimateId) {
    log("Create an estimate version first.");
    return;
  }
  try {
    const data = await apiRequest(`/estimate-versions/${state.estimateId}/lock`, "POST");
    setEstimateMeta(data.is_locked);
    log("Estimate locked.");
  } catch (err) {
    log(`Lock error: ${err.message}`);
  }
});

document.getElementById("refresh-btn").addEventListener("click", async () => {
  if (!state.estimateId) {
    log("Create an estimate version first.");
    return;
  }
  try {
    const data = await apiRequest(`/estimate-versions/${state.estimateId}`, "GET");
    renderEstimate(data);
    log("Estimate refreshed.");
  } catch (err) {
    log(`Refresh error: ${err.message}`);
  }
});

setProjectMeta();
setEstimateMeta();
