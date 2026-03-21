const state = {
  projectId: null,
  estimateId: null,
  extraMaterials: [],
};

const appConfig = {
  locale: "en-NG",
  currency: "NGN",
};

function log(message) {
  const el = document.getElementById("log");
  const time = new Date().toLocaleTimeString();
  el.textContent = `[${time}] ${message}\n` + el.textContent;
}

function toMoney(value) {
  return new Intl.NumberFormat(appConfig.locale, {
    style: "currency",
    currency: appConfig.currency,
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function setProjectMeta() {
  const meta = document.getElementById("project-meta");
  meta.textContent = state.projectId ? `Project ID: ${state.projectId}` : "No project selected.";
}

function normalizeKey(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "custom_material";
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

function renderPriceInputs(materials) {
  const tbody = document.getElementById("price-input-rows");
  tbody.innerHTML = "";
  materials.forEach((item) => {
    const suggested = `${toMoney(item.unit_price_low)} - ${toMoney(item.unit_price_high)}`;
    const currentKnown =
      item.price_source === "user_provided" ? item.unit_price_expected : "";
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${item.item_name}</strong><br /><small>${item.material_key}</small></td>
      <td>${item.quantity.toFixed(2)}</td>
      <td>${item.unit}</td>
      <td>${suggested}</td>
      <td>
        <input
          class="price-input"
          type="number"
          step="0.01"
          min="0"
          data-material-key="${item.material_key}"
          value="${currentKnown}"
          placeholder="e.g. 12.50"
        />
      </td>
    `;
    tbody.appendChild(row);
  });
  document.getElementById("price-prompt").textContent =
    "Enter any prices you know, then click 'Apply Prices & Recalculate'.";
}

function renderAutoEstimate(data) {
  const tbody = document.getElementById("auto-line-items");
  tbody.innerHTML = "";

  data.materials.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.item_name}${item.needs_user_price ? " <em>(range)</em>" : " <em>(fixed)</em>"}</td>
      <td>${item.quantity.toFixed(2)}</td>
      <td>${toMoney(item.unit_price_low)} - ${toMoney(item.unit_price_high)}</td>
      <td>${toMoney(item.unit_price_expected)}</td>
      <td>${toMoney(item.line_total_low)} - ${toMoney(item.line_total_high)}</td>
      <td>${item.price_source}</td>
      <td>${item.confidence_score.toFixed(1)}</td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("auto-total-range").textContent = `${toMoney(data.totals.total_low)} - ${toMoney(data.totals.total_high)}`;
  document.getElementById("auto-total-expected").textContent = toMoney(data.totals.total_expected);
  const meta = document.getElementById("drawing-meta");
  meta.textContent = `Drawing: ${data.file_name} | Assumptions: ${data.assumptions.floor_area_sqm} sqm, ${data.assumptions.floors} floor(s), ${data.assumptions.bedrooms} bed, ${data.assumptions.bathrooms} bath`;
  renderPriceInputs(data.materials);
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

function collectKnownPricesFromInputs() {
  const prices = {};
  document.querySelectorAll("#price-input-rows input[data-material-key]").forEach((input) => {
    const value = Number(input.value);
    if (Number.isFinite(value) && value > 0) {
      prices[input.dataset.materialKey] = value;
    }
  });
  state.extraMaterials.forEach((item) => {
    if (item.known_unit_price && Number(item.known_unit_price) > 0) {
      prices[item.material_key] = Number(item.known_unit_price);
    }
  });
  return prices;
}

function renderExtraMaterials() {
  const tbody = document.getElementById("extra-material-rows");
  tbody.innerHTML = "";
  state.extraMaterials.forEach((item, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.item_name}<br /><small>${item.material_key}</small></td>
      <td>${item.quantity.toFixed(2)}</td>
      <td>${item.unit}</td>
      <td>${item.known_unit_price ? toMoney(item.known_unit_price) : "not set"}</td>
      <td><button type="button" data-remove-extra="${index}">Remove</button></td>
    `;
    tbody.appendChild(row);
  });
}

async function runDrawingEstimate() {
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

  const knownPrices = collectKnownPricesFromInputs();
  const formData = new FormData();
  formData.append("drawing", file);
  formData.append("known_prices_json", JSON.stringify(knownPrices));
  formData.append("additional_materials_json", JSON.stringify(state.extraMaterials));

  const data = await apiFormRequest(`/projects/${state.projectId}/drawings/auto-estimate`, formData);
  renderAutoEstimate(data);

  if (data.missing_unit_price_items.length > 0) {
    log(`Recalculated. You can still add prices for: ${data.missing_unit_price_items.join(", ")}`);
  } else {
    log("All materials now have known prices.");
  }
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
    state.extraMaterials = [];
    renderExtraMaterials();
    document.getElementById("price-input-rows").innerHTML = "";
    document.getElementById("auto-line-items").innerHTML = "";
    document.getElementById("auto-total-range").textContent = "$0.00 - $0.00";
    document.getElementById("auto-total-expected").textContent = "$0.00";
    document.getElementById("price-prompt").textContent = "Analyze a drawing first to populate this list.";
    document.getElementById("drawing-meta").textContent = "No drawing analyzed yet.";
    setProjectMeta();
    setEstimateMeta();
    log(`Created project "${data.name}" (id=${data.id})`);
  } catch (err) {
    log(`Project error: ${err.message}`);
  }
});

document.getElementById("drawing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await runDrawingEstimate();
    log("Drawing analysis complete.");
  } catch (err) {
    log(`Drawing estimate error: ${err.message}`);
  }
});

document.getElementById("apply-price-btn").addEventListener("click", async () => {
  try {
    await runDrawingEstimate();
  } catch (err) {
    log(`Price update error: ${err.message}`);
  }
});

document.getElementById("extra-material-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const itemName = document.getElementById("extra-name").value.trim();
  const csiCode = document.getElementById("extra-csi").value.trim() || "CUSTOM-00";
  const quantity = Number(document.getElementById("extra-qty").value);
  const unit = document.getElementById("extra-unit").value.trim();
  const waste = Number(document.getElementById("extra-waste").value);
  const knownPriceInput = document.getElementById("extra-price").value.trim();
  const knownPrice = knownPriceInput ? Number(knownPriceInput) : null;

  if (!itemName || !unit || !Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(waste) || waste < 0) {
    log("Invalid extra material values.");
    return;
  }
  if (knownPrice !== null && (!Number.isFinite(knownPrice) || knownPrice <= 0)) {
    log("Known unit price for extra material must be positive.");
    return;
  }

  state.extraMaterials.push({
    material_key: normalizeKey(itemName),
    item_name: itemName,
    csi_code: csiCode,
    quantity,
    unit,
    waste_factor_pct: waste,
    known_unit_price: knownPrice,
  });
  renderExtraMaterials();
  event.target.reset();
  document.getElementById("extra-waste").value = "0";
  log(`Added extra material "${itemName}". Recalculate to include it.`);
});

document.getElementById("extra-material-rows").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) {
    return;
  }
  const index = target.dataset.removeExtra;
  if (index === undefined) {
    return;
  }
  const idx = Number(index);
  const removed = state.extraMaterials[idx];
  state.extraMaterials = state.extraMaterials.filter((_, i) => i !== idx);
  renderExtraMaterials();
  if (removed) {
    log(`Removed extra material "${removed.item_name}".`);
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
renderExtraMaterials();
