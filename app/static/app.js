const state = {
  projectId: null,
  projectName: null,
  estimateId: null,
  extraMaterials: [],
  materialVariants: {},
};

const appConfig = {
  locale: "en-NG",
  currency: "NGN",
};

const MATERIAL_VARIANTS = {
  cement_bag: [
    { key: "market", label: "Market blend (auto)", multiplier: null, autofill: false },
    { key: "dangote_50kg", label: "Dangote Cement 50kg", multiplier: 1.05, autofill: true },
    { key: "bua_50kg", label: "BUA Cement 50kg", multiplier: 1.01, autofill: true },
    { key: "lafarge_50kg", label: "Lafarge Cement 50kg", multiplier: 1.04, autofill: true },
  ],
  rebar_kg: [
    { key: "market", label: "Market blend (auto)", multiplier: null, autofill: false },
    { key: "austen", label: "Austen Rebar", multiplier: 1.03, autofill: true },
    { key: "african_foundries", label: "African Foundries", multiplier: 1.01, autofill: true },
    { key: "local_mill", label: "Local mill (budget)", multiplier: 0.96, autofill: true },
  ],
  paint_liter: [
    { key: "market", label: "Market blend (auto)", multiplier: null, autofill: false },
    { key: "dulux", label: "Dulux", multiplier: 1.1, autofill: true },
    { key: "berger", label: "Berger", multiplier: 1.06, autofill: true },
    { key: "sandtex", label: "Sandtex", multiplier: 1.0, autofill: true },
  ],
};

function log(message) {
  const el = document.getElementById("log");
  const time = new Date().toLocaleTimeString();
  if (!el) {
    console.log(`[${time}] ${message}`);
    return;
  }
  el.textContent = `[${time}] ${message}\n` + el.textContent;
}

function toMoney(value) {
  return new Intl.NumberFormat(appConfig.locale, {
    style: "currency",
    currency: appConfig.currency,
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function normalizeKey(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "custom_material";
}

function setStatus(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) {
    return;
  }
  el.textContent = text;
  el.classList.remove("pending", "success", "running");
  el.classList.add(kind);
}

function setButtonLoading(id, isLoading, idleText, loadingText) {
  const btn = document.getElementById(id);
  if (!btn) {
    return;
  }
  btn.disabled = isLoading;
  btn.textContent = isLoading ? loadingText : idleText;
}

function setProjectMeta() {
  const meta = document.getElementById("project-meta");
  meta.textContent = state.projectId
    ? `Project created: ${state.projectName || "Untitled"} (ID ${state.projectId})`
    : "No project selected. Project will auto-create when you click Analyze Drawing.";
}

function setEstimateMeta(locked = false) {
  const meta = document.getElementById("estimate-meta");
  if (!state.estimateId) {
    meta.textContent = "No estimate selected.";
    return;
  }
  meta.textContent = `Estimate ID: ${state.estimateId}${locked ? " (locked)" : ""}`;
}

function defaultVariantKey(item) {
  if (item.material_key === "cement_bag") {
    return "dangote_50kg";
  }
  return "market";
}

function variantsForMaterial(item) {
  return MATERIAL_VARIANTS[item.material_key] || [
    { key: "market", label: "Market blend (auto)", multiplier: null, autofill: false },
  ];
}

function variantPrice(item, variantKey) {
  const variant = variantsForMaterial(item).find((row) => row.key === variantKey);
  if (!variant || variant.multiplier === null) {
    return null;
  }
  return Number((item.unit_price_expected * variant.multiplier).toFixed(2));
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
    const variantList = variantsForMaterial(item);
    const selectedVariant =
      state.materialVariants[item.material_key] || defaultVariantKey(item);
    state.materialVariants[item.material_key] = selectedVariant;
    const suggested = `${toMoney(item.unit_price_low)} - ${toMoney(item.unit_price_high)}`;
    const currentKnown =
      item.price_source === "user_provided"
        ? item.unit_price_expected
        : variantPrice(item, selectedVariant) || "";

    const optionTags = variantList
      .map(
        (option) =>
          `<option value="${option.key}" ${option.key === selectedVariant ? "selected" : ""}>${option.label}</option>`,
      )
      .join("");

    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${item.item_name}</strong><br /><small>${item.material_key}</small></td>
      <td>${item.quantity.toFixed(2)}</td>
      <td>${item.unit}</td>
      <td>
        <select
          class="material-type-select"
          data-material-key="${item.material_key}"
          data-expected="${item.unit_price_expected}"
        >
          ${optionTags}
        </select>
      </td>
      <td>${suggested}</td>
      <td>
        <input
          class="price-input"
          type="number"
          step="0.01"
          min="0"
          data-material-key="${item.material_key}"
          value="${currentKnown}"
          placeholder="Enter known unit price"
        />
      </td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("price-prompt").textContent =
    "Detected materials are locked. Pick material type (e.g., Dangote cement) and enter any exact prices you know.";
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

  document.getElementById("auto-total-range").textContent =
    `${toMoney(data.totals.total_low)} - ${toMoney(data.totals.total_high)}`;
  document.getElementById("auto-total-expected").textContent = toMoney(data.totals.total_expected);
  document.getElementById("headline-total-range").textContent =
    `${toMoney(data.totals.total_low)} - ${toMoney(data.totals.total_high)}`;
  document.getElementById("headline-total-expected").textContent = toMoney(data.totals.total_expected);
  const meta = document.getElementById("drawing-meta");
  meta.textContent =
    `Drawing: ${data.file_name} | Assumptions: ${data.assumptions.floor_area_sqm} sqm, ` +
    `${data.assumptions.floors} floor(s), ${data.assumptions.bedrooms} bed, ${data.assumptions.bathrooms} bath, quality ${data.assumptions.quality_level}. ` +
    `${data.benchmark_adjustment_applied ? "Benchmark adjustment applied." : "No benchmark adjustment."}`;
  setStatus("drawing-status", "Completed", "success");
  setStatus("total-status", `Expected ${toMoney(data.totals.total_expected)}`, "success");
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

function projectPayloadFromForm() {
  const name = (document.getElementById("project-name").value || "").trim() || "Quick Estimate";
  const projectType = (document.getElementById("project-type").value || "").trim() || "residential";
  const location = (document.getElementById("project-zip").value || "").trim() || "Lagos";
  return { name, project_type: projectType, zip_code: location };
}

function resetCostViews() {
  document.getElementById("price-input-rows").innerHTML = "";
  document.getElementById("auto-line-items").innerHTML = "";
  document.getElementById("auto-total-range").textContent = `${toMoney(0)} - ${toMoney(0)}`;
  document.getElementById("auto-total-expected").textContent = toMoney(0);
  document.getElementById("headline-total-range").textContent = `${toMoney(0)} - ${toMoney(0)}`;
  document.getElementById("headline-total-expected").textContent = toMoney(0);
  document.getElementById("price-prompt").textContent = "Analyze a drawing first to populate this list.";
  document.getElementById("drawing-meta").textContent = "No drawing analyzed yet.";
  setStatus("drawing-status", "Not started", "pending");
  setStatus("total-status", "No estimate yet", "pending");
}

async function ensureProject() {
  if (state.projectId) {
    return state.projectId;
  }
  setButtonLoading("project-create-btn", true, "Create Project", "Creating...");
  setStatus("project-status", "Creating...", "running");
  const payload = projectPayloadFromForm();
  const data = await apiRequest("/projects", "POST", payload);
  state.projectId = data.id;
  state.projectName = data.name;
  state.estimateId = null;
  state.extraMaterials = [];
  state.materialVariants = {};
  renderExtraMaterials();
  setProjectMeta();
  setEstimateMeta();
  resetCostViews();
  setStatus("project-status", `Created (#${data.id})`, "success");
  setButtonLoading("project-create-btn", false, "Create Project", "Creating...");
  log(`Project ready: "${data.name}" (id=${data.id})`);
  return data.id;
}

function collectKnownPricesFromInputs() {
  const prices = {};
  document.querySelectorAll("#price-input-rows input[data-material-key]").forEach((input) => {
    const value = Number(input.value);
    if (Number.isFinite(value) && value > 0) {
      prices[input.dataset.materialKey] = value;
    }
  });

  document.querySelectorAll("#price-input-rows select[data-material-key]").forEach((select) => {
    const key = select.dataset.materialKey;
    if (!key || prices[key]) {
      return;
    }
    const selected = select.value;
    const expected = Number(select.dataset.expected || "0");
    const fakeItem = { material_key: key, unit_price_expected: expected };
    const suggested = variantPrice(fakeItem, selected);
    if (suggested && suggested > 0) {
      prices[key] = suggested;
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
  await ensureProject();
  const fileInput = document.getElementById("drawing-file");
  const file = fileInput.files[0];
  if (!file) {
    throw new Error("Select a drawing file first.");
  }

  setStatus("drawing-status", "Analyzing...", "running");
  setButtonLoading("analyze-btn", true, "Analyze Drawing", "Analyzing...");
  setButtonLoading("apply-price-btn", true, "Apply Prices & Recalculate", "Recalculating...");

  try {
    const knownPrices = collectKnownPricesFromInputs();
    const assumptionPayload = {
      floor_area_sqm: document.getElementById("assumption-area").value || null,
      floors: document.getElementById("assumption-floors").value || null,
      bedrooms: document.getElementById("assumption-bedrooms").value || null,
      bathrooms: document.getElementById("assumption-bathrooms").value || null,
      quality_level: document.getElementById("assumption-quality").value || "standard",
    };
    const formData = new FormData();
    formData.append("drawing", file);
    formData.append("known_prices_json", JSON.stringify(knownPrices));
    formData.append("additional_materials_json", JSON.stringify(state.extraMaterials));
    formData.append("assumptions_json", JSON.stringify(assumptionPayload));

    const data = await apiFormRequest(`/projects/${state.projectId}/drawings/auto-estimate`, formData);
    renderAutoEstimate(data);
    if (data.missing_unit_price_items.length > 0) {
      log(`Analysis complete. Missing price inputs: ${data.missing_unit_price_items.length} item(s).`);
    } else {
      log("Analysis complete. All detected materials have explicit unit prices.");
    }
  } finally {
    setButtonLoading("analyze-btn", false, "Analyze Drawing", "Analyzing...");
    setButtonLoading("apply-price-btn", false, "Apply Prices & Recalculate", "Recalculating...");
  }
}

document.getElementById("project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (state.projectId) {
      log(`Project already created (id=${state.projectId}).`);
      return;
    }
    await ensureProject();
  } catch (err) {
    setStatus("project-status", "Failed", "pending");
    setButtonLoading("project-create-btn", false, "Create Project", "Creating...");
    log(`Project error: ${err.message}`);
  }
});

document.getElementById("drawing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await runDrawingEstimate();
  } catch (err) {
    setStatus("drawing-status", "Failed", "pending");
    log(`Drawing estimate error: ${err.message}`);
  }
});

document.getElementById("apply-price-btn").addEventListener("click", async () => {
  try {
    await runDrawingEstimate();
  } catch (err) {
    setStatus("drawing-status", "Failed", "pending");
    log(`Price update error: ${err.message}`);
  }
});

document.getElementById("price-input-rows").addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLSelectElement)) {
    return;
  }
  if (!target.matches("select[data-material-key]")) {
    return;
  }
  const materialKey = target.dataset.materialKey;
  if (!materialKey) {
    return;
  }
  state.materialVariants[materialKey] = target.value;
  const row = target.closest("tr");
  if (!row) {
    return;
  }
  const input = row.querySelector(`input[data-material-key="${materialKey}"]`);
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  const expected = Number(target.dataset.expected || "0");
  const suggested = variantPrice({ material_key: materialKey, unit_price_expected: expected }, target.value);
  if (suggested) {
    input.value = String(suggested);
    log(`Applied ${target.options[target.selectedIndex].text} for ${materialKey}.`);
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
  log(`Added extra material "${itemName}". Click Apply/Recalculate to include it.`);
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
  try {
    await ensureProject();
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
  try {
    await ensureProject();
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
  try {
    await ensureProject();
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
resetCostViews();
setStatus("project-status", "Not created", "pending");
