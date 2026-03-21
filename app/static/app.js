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

function parseKnownPrices(input) {
  const lines = input
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  const payload = {};
  lines.forEach((line) => {
    const [key, rawValue] = line.includes("=") ? line.split("=") : line.split(":");
    if (!key || rawValue === undefined) {
      return;
    }
    const numericValue = Number(rawValue.trim());
    if (Number.isFinite(numericValue) && numericValue > 0) {
      payload[key.trim()] = numericValue;
    }
  });
  return payload;
}

function renderAutoEstimate(data) {
  const tbody = document.getElementById("auto-line-items");
  tbody.innerHTML = "";

  data.materials.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.item_name}${item.needs_user_price ? " <em>(market range)</em>" : ""}</td>
      <td>${item.quantity.toFixed(2)}</td>
      <td>${item.unit}</td>
      <td>${toMoney(item.unit_price_low)} - ${toMoney(item.unit_price_high)}</td>
      <td>${toMoney(item.unit_price_expected)}</td>
      <td>${toMoney(item.line_total_low)} - ${toMoney(item.line_total_high)}</td>
      <td>${item.price_source}</td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("auto-total-range").textContent = `${toMoney(data.totals.total_low)} - ${toMoney(data.totals.total_high)}`;
  document.getElementById("auto-total-expected").textContent = toMoney(data.totals.total_expected);
  const meta = document.getElementById("drawing-meta");
  meta.textContent = `Drawing: ${data.file_name} | Assumptions: ${data.assumptions.floor_area_sqm} sqm, ${data.assumptions.floors} floor(s), ${data.assumptions.bedrooms} bed, ${data.assumptions.bathrooms} bath`;
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

async function apiFormRequest(path, formData) {
  const response = await fetch(path, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const message = error.detail || `Request failed (${response.status})`;
    throw new Error(message);
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

document.getElementById("drawing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectId) {
    log("Create a project first.");
    return;
  }
  const fileInput = document.getElementById("drawing-file");
  const file = fileInput.files[0];
  if (!file) {
    log("Select a drawing file first.");
    return;
  }

  try {
    const knownPricesRaw = document.getElementById("known-prices").value;
    const knownPrices = parseKnownPrices(knownPricesRaw);
    const formData = new FormData();
    formData.append("drawing", file);
    formData.append("known_prices_json", JSON.stringify(knownPrices));

    const data = await apiFormRequest(`/projects/${state.projectId}/drawings/auto-estimate`, formData);
    renderAutoEstimate(data);
    if (data.missing_unit_price_items.length > 0) {
      log(`Auto-estimate done. Add known prices for: ${data.missing_unit_price_items.join(", ")}`);
    } else {
      log("Auto-estimate done with your provided unit prices.");
    }
  } catch (err) {
    log(`Drawing estimate error: ${err.message}`);
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
