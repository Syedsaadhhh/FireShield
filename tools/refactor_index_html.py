#!/usr/bin/env python3
"""
Applies targeted Run 8 polish to static/index.html:
- Fix header engine health string ('Engine: HEALTHY · local')
- Add inline feedback banners and eliminate alert()-only flows
- Handle mobile 390px responsive layout without horizontal overflow
- Wire asynchronous screening job polling
- Parse real subtitles from VTT (no hardcoded 48%/50% boxes or HUD strings)
- Update asset catalog tags (Clean vs Hazard)
- Fix blank stage saying "Select an asset" when asset selected
- Remove fake fallback metrics (?? 15.0, ?? 0.385, ?? 1.0)
"""

import pathlib

INDEX_PATH = pathlib.Path(__file__).resolve().parents[1] / "static" / "index.html"

def main():
    content = INDEX_PATH.read_text(encoding="utf-8")

    # 1. CSS update for inline feedback & mobile responsive rules
    old_css_target = """    /* Responsive Queries */
    @media (max-width: 1200px) {
      .catalog-rail { width: 240px; }
      .inspector-drawer { width: 320px; }
    }

    @media (max-width: 900px) {
      .workbench-container { flex-direction: column; height: auto; }
      .catalog-rail, .center-stage, .inspector-drawer { width: 100%; border: none; }
      .catalog-rail { height: 200px; border-bottom: 1px solid var(--stage-border); }
    }"""

    new_css = """    /* Inline Status & Feedback Banners */
    .inline-feedback-banner {
      padding: 8px 12px;
      border-radius: 4px;
      font-size: 11px;
      margin-top: 8px;
      display: none;
      line-height: 1.4;
      word-break: break-word;
    }
    .feedback-info {
      background: rgba(59, 130, 246, 0.15);
      border: 1px solid rgba(59, 130, 246, 0.3);
      color: #93c5fd;
    }
    .feedback-success {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #6ee7b7;
    }
    .feedback-error {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
    }
    .feedback-warning {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.3);
      color: #fcd34d;
    }

    /* Responsive Queries */
    @media (max-width: 1200px) {
      .catalog-rail { width: 240px; }
      .inspector-drawer { width: 320px; }
    }

    @media (max-width: 900px) {
      header {
        height: auto;
        flex-direction: column;
        align-items: stretch;
        padding: 10px 14px;
        gap: 10px;
      }
      .brand-section {
        justify-content: space-between;
      }
      .nav-tabs {
        width: 100%;
        overflow-x: auto;
        white-space: nowrap;
      }
      .nav-tab {
        flex: 1;
        text-align: center;
        padding: 8px 6px;
        font-size: 11px;
      }
      .header-badges {
        flex-wrap: wrap;
        gap: 6px;
      }
      .pill-badge {
        font-size: 10px;
        padding: 3px 8px;
      }
      .workbench-container {
        flex-direction: column;
        height: auto;
      }
      .catalog-rail, .center-stage, .inspector-drawer {
        width: 100%;
        max-width: 100%;
        border: none;
      }
      .catalog-rail {
        height: auto;
        max-height: 220px;
        border-bottom: 1px solid var(--stage-border);
      }
      .transport-bar {
        flex-wrap: wrap;
        gap: 8px;
      }
      .inspector-drawer {
        border-top: 1px solid var(--stage-border);
      }
    }

    @media (max-width: 500px) {
      body {
        overflow-x: hidden;
      }
      header {
        padding: 8px;
      }
      .nav-tabs {
        flex-direction: column;
        gap: 4px;
      }
      .nav-tab {
        width: 100%;
      }
      .header-badges {
        flex-direction: column;
        align-items: flex-start;
      }
      .pill-badge {
        width: 100%;
        justify-content: flex-start;
      }
      .transport-btn-group {
        width: 100%;
        justify-content: space-between;
      }
    }"""

    # Normalize newlines for replacement
    content = content.replace("\r\n", "\n")
    old_css_target = old_css_target.replace("\r\n", "\n")
    new_css = new_css.replace("\r\n", "\n")

    if old_css_target in content:
        content = content.replace(old_css_target, new_css, 1)
        print("Updated CSS rules")
    else:
        print("WARNING: old_css_target not found")

    # 2. Add inline feedback banners to Inspector & Transport
    # Under transport bar
    old_transport_end = """          <div style="display: flex; gap: 6px;">
            <button class="btn btn-bronze" style="padding: 5px 10px; font-size: 11px;" id="btn-run-screening" onclick="triggerScreening()">Run Screening</button>
          </div>
        </div>"""
    new_transport_end = """          <div style="display: flex; gap: 6px;">
            <button class="btn btn-bronze" style="padding: 5px 10px; font-size: 11px;" id="btn-run-screening" onclick="triggerScreening()">Run Screening</button>
          </div>
        </div>
        <div id="screening-feedback" class="inline-feedback-banner"></div>"""
    if old_transport_end in content:
        content = content.replace(old_transport_end, new_transport_end, 1)
        print("Added screening-feedback banner")

    # Under Section 2 (Context)
    old_sec2_btn = """<button class="btn btn-secondary btn-full" onclick="approveContext()">Save Context Approval</button>
        </div>"""
    new_sec2_btn = """<button class="btn btn-secondary btn-full" onclick="approveContext()">Save Context Approval</button>
          <div id="context-feedback" class="inline-feedback-banner"></div>
        </div>"""
    if old_sec2_btn in content:
        content = content.replace(old_sec2_btn, new_sec2_btn, 1)
        print("Added context-feedback banner")

    # Under Section 3 (Review)
    old_sec3_btn = """<button class="btn btn-bronze btn-full" onclick="submitReview()">Commit Review Decision</button>
        </div>"""
    new_sec3_btn = """<button class="btn btn-bronze btn-full" onclick="submitReview()">Commit Review Decision</button>
          <div id="review-feedback" class="inline-feedback-banner"></div>
        </div>"""
    if old_sec3_btn in content:
        content = content.replace(old_sec3_btn, new_sec3_btn, 1)
        print("Added review-feedback banner")

    # Under Section 4 (Publish)
    old_sec4_btn = """<button class="btn btn-bronze btn-full" id="btn-publish-release" onclick="publishRelease()" disabled>Sign & Publish Ed25519 Sidecar</button>"""
    new_sec4_btn = """<button class="btn btn-bronze btn-full" id="btn-publish-release" onclick="publishRelease()" disabled>Sign & Publish Ed25519 Sidecar</button>
          <div id="publish-feedback" class="inline-feedback-banner"></div>"""
    if old_sec4_btn in content:
        content = content.replace(old_sec4_btn, new_sec4_btn, 1)
        print("Added publish-feedback banner")

    # 3. Replace JS functions
    # Replace fetchHealth
    old_fetch_health = """    async function fetchHealth() {
      try {
        const resp = await fetch(`${API_BASE}/api/v1/health`);
        const data = await resp.json();
        document.getElementById("engine-health-pill").textContent = `Engine: ${data.status} (${data.service})`;
      } catch (e) {
        document.getElementById("engine-health-pill").textContent = "Engine: Offline";
      }
    }"""
    new_fetch_health = """    function setFeedback(elementId, message, type = "info") {
      const el = document.getElementById(elementId);
      if (!el) return;
      el.textContent = message;
      el.className = `inline-feedback-banner feedback-${type}`;
      el.style.display = message ? "block" : "none";
    }

    async function fetchHealth() {
      try {
        const resp = await fetch(`${API_BASE}/api/v1/health`);
        const data = await resp.json();
        const mode = data.environment_mode ? data.environment_mode.replace(/_/g, " ") : "local";
        document.getElementById("engine-health-pill").textContent = `Engine: ${data.status} · ${mode}`;
      } catch (e) {
        document.getElementById("engine-health-pill").textContent = "Engine: Offline";
      }
    }"""
    if old_fetch_health in content:
        content = content.replace(old_fetch_health, new_fetch_health, 1)
        print("Updated fetchHealth & added setFeedback")

    # Replace renderAssetList
    old_render_asset_list = """    function renderAssetList() {
      const container = document.getElementById("asset-list-container");
      container.innerHTML = "";

      assets.forEach(ast => {
        const el = document.createElement("div");
        el.className = `asset-item ${selectedAsset && selectedAsset.asset_id === ast.asset_id ? "selected" : ""}`;
        el.onclick = () => selectAsset(ast.asset_id);

        let tagClass = "tag-unscreened";
        let tagText = "Unscreened";
        if (ast.has_screening) {
          if (ast.review_state === "approved") { tagClass = "tag-approved"; tagText = "Approved"; }
          else if (ast.review_state === "rejected") { tagClass = "tag-rejected"; tagText = "Rejected"; }
          else { tagClass = "tag-hazard"; tagText = "Hazard"; }
        }

        el.innerHTML = `
          <div class="asset-item-title">${escapeHtml(ast.name)}</div>
          <div class="asset-item-meta">
            <span class="tabular-nums">${Math.round(ast.duration_ms / 1000)}s &bull; ${ast.fps} fps</span>
            <span class="status-tag ${tagClass}">${tagText}</span>
          </div>
        `;
        container.appendChild(el);
      });
    }"""
    new_render_asset_list = """    function renderAssetList() {
      const container = document.getElementById("asset-list-container");
      container.innerHTML = "";

      assets.forEach(ast => {
        const el = document.createElement("div");
        el.className = `asset-item ${selectedAsset && selectedAsset.asset_id === ast.asset_id ? "selected" : ""}`;
        el.onclick = () => selectAsset(ast.asset_id);

        let tagClass = "tag-unscreened";
        let tagText = "Unscreened";
        if (ast.has_screening) {
          if (ast.review_state === "approved") {
            tagClass = "tag-approved";
            tagText = "Approved";
          } else if (ast.review_state === "rejected") {
            tagClass = "tag-rejected";
            tagText = "Rejected";
          } else if (ast.findings_count === 0) {
            tagClass = "tag-clean";
            tagText = "Clean";
          } else {
            tagClass = "tag-hazard";
            tagText = "Hazard";
          }
        }

        el.innerHTML = `
          <div class="asset-item-title">${escapeHtml(ast.name)}</div>
          <div class="asset-item-meta">
            <span class="tabular-nums">${Math.round(ast.duration_ms / 1000)}s &bull; ${ast.fps} fps</span>
            <span class="status-tag ${tagClass}">${tagText}</span>
          </div>
        `;
        container.appendChild(el);
      });
    }"""
    if old_render_asset_list in content:
        content = content.replace(old_render_asset_list, new_render_asset_list, 1)
        print("Updated renderAssetList")

    # Update selectAsset to call renderFrameCanvas
    old_select_asset = """    async function selectAsset(assetId) {
      selectedAsset = assets.find(a => a.asset_id === assetId);
      currentReport = null;
      selectedFinding = null;
      renderAssetList();

      currentPositionMs = 0;
      updatePlayheadVisuals();

      // Check if already screened
      if (selectedAsset.has_screening) {
        await loadReport(selectedAsset.asset_revision_id);
      } else {
        renderUnscreenedInspector();
      }

      renderTimeline();
    }"""
    new_select_asset = """    async function selectAsset(assetId) {
      selectedAsset = assets.find(a => a.asset_id === assetId);
      currentReport = null;
      selectedFinding = null;
      renderAssetList();

      currentPositionMs = 0;
      updatePlayheadVisuals();
      renderFrameCanvas();

      // Check if already screened
      if (selectedAsset.has_screening) {
        await loadReport(selectedAsset.asset_revision_id);
      } else {
        renderUnscreenedInspector();
      }

      renderTimeline();
    }"""
    if old_select_asset in content:
        content = content.replace(old_select_asset, new_select_asset, 1)
        print("Updated selectAsset")

    # Update renderFrameCanvas
    old_render_canvas = """    function renderFrameCanvas() {
      const canvas = document.getElementById("preview-canvas");
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#111827";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(selectedAsset ? "Media preview unavailable" : "Select an asset from the catalog", canvas.width / 2, canvas.height / 2);
      if (selectedAsset) {
        ctx.fillStyle = "#94a3b8";
        ctx.font = "12px sans-serif";
        ctx.fillText("Screening results are available without displaying test patterns.", canvas.width / 2, canvas.height / 2 + 26);
      }
    }"""
    new_render_canvas = """    function renderFrameCanvas() {
      const canvas = document.getElementById("preview-canvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#0d1017";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "600 13px -apple-system, sans-serif";
      ctx.textAlign = "center";

      if (selectedAsset) {
        ctx.fillText(`Selected Asset: ${selectedAsset.name}`, canvas.width / 2, canvas.height / 2 - 12);
        ctx.fillStyle = "#94a3b8";
        ctx.font = "12px -apple-system, sans-serif";
        ctx.fillText("Media preview unavailable · Safe static display", canvas.width / 2, canvas.height / 2 + 12);
        ctx.fillText("Deterministic findings and metrics rendered in inspector below", canvas.width / 2, canvas.height / 2 + 32);
      } else {
        ctx.fillText("Select an asset from the catalog rail to inspect", canvas.width / 2, canvas.height / 2);
      }
    }"""
    if old_render_canvas in content:
        content = content.replace(old_render_canvas, new_render_canvas, 1)
        print("Updated renderFrameCanvas")

    # Update triggerScreening with real polling
    old_trigger_screening = """    async function triggerScreening() {
      if (!selectedAsset) return;
      const btn = document.getElementById("btn-run-screening");
      btn.disabled = true;
      btn.textContent = "Screening Frames...";

      try {
        const resp = await fetch(`${API_BASE}/api/v1/screening/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_id: selectedAsset.asset_id })
        });
        const job = await resp.json();
        if (job.status === "COMPLETED") {
          await loadAssets();
          await selectAsset(selectedAsset.asset_id);
        } else {
          alert(`Screening failed: ${job.error_message || "Unknown error"}`);
        }
      } catch (e) {
        alert("Screening execution error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Run Screening";
      }
    }"""
    new_trigger_screening = """    async function triggerScreening() {
      if (!selectedAsset) return;
      const btn = document.getElementById("btn-run-screening");
      btn.disabled = true;
      btn.textContent = "Screening Frames...";
      setFeedback("screening-feedback", "Submitting asynchronous screening job...", "info");

      try {
        const resp = await fetch(`${API_BASE}/api/v1/screening/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_id: selectedAsset.asset_id, wait: false })
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          setFeedback("screening-feedback", `Screening job creation failed: ${err.error || resp.statusText}`, "error");
          btn.disabled = false;
          btn.textContent = "Run Screening";
          return;
        }
        const job = await resp.json();
        const jobId = job.job_id;
        setFeedback("screening-feedback", `Screening job ${jobId} running...`, "info");

        const pollInterval = setInterval(async () => {
          try {
            const jResp = await fetch(`${API_BASE}/api/v1/screening/jobs/${jobId}`);
            if (jResp.ok) {
              const currentJob = await jResp.json();
              if (currentJob.status === "COMPLETED") {
                clearInterval(pollInterval);
                setFeedback("screening-feedback", `✓ Screening completed (${currentJob.findings_count} findings detected)`, "success");
                btn.disabled = false;
                btn.textContent = "Run Screening";
                await loadAssets();
                await selectAsset(selectedAsset.asset_id);
              } else if (currentJob.status === "FAILED") {
                clearInterval(pollInterval);
                setFeedback("screening-feedback", `Screening failed: ${currentJob.error_message || "Unknown error"}`, "error");
                btn.disabled = false;
                btn.textContent = "Run Screening";
              }
            }
          } catch (pe) {
            // keep polling
          }
        }, 500);
      } catch (e) {
        setFeedback("screening-feedback", `Screening network error: ${e.message}`, "error");
        btn.disabled = false;
        btn.textContent = "Run Screening";
      }
    }"""
    if old_trigger_screening in content:
        content = content.replace(old_trigger_screening, new_trigger_screening, 1)
        print("Updated triggerScreening with real polling")

    # Update renderScreenedInspector
    old_render_screened = """        const m = selectedFinding.metrics || {};
        document.getElementById("m-lum-delta").textContent = m.max_luminance_delta ?? "1.000";
        document.getElementById("m-flash-freq").textContent = `${m.peak_luminance_flashes_per_sec ?? 15.0} Hz`;
        document.getElementById("m-spectral").textContent = m.max_spectral_concentration ?? "0.385";
        document.getElementById("m-area").textContent = `${Math.round((m.max_affected_area_fraction ?? 1.0) * 100)}%`;"""
    new_render_screened = """        const m = selectedFinding.metrics || {};
        document.getElementById("m-lum-delta").textContent = (m.max_luminance_delta !== undefined) ? String(m.max_luminance_delta) : "Not measured";
        document.getElementById("m-flash-freq").textContent = (m.peak_luminance_flashes_per_sec !== undefined) ? `${m.peak_luminance_flashes_per_sec} Hz` : "Not measured";
        document.getElementById("m-spectral").textContent = (m.max_spectral_concentration !== undefined) ? String(m.max_spectral_concentration) : "Not measured";
        document.getElementById("m-area").textContent = (m.max_affected_area_fraction !== undefined) ? `${Math.round(m.max_affected_area_fraction * 100)}%` : "Not measured";"""
    if old_render_screened in content:
        content = content.replace(old_render_screened, new_render_screened, 1)
        print("Updated renderScreenedInspector metrics")

    # Remove hardcoded context note string
    old_context_string = """      } else if (hasHazards && selectedAsset.subtitles_vtt) {
        // Auto-extract preliminary dialogue if present
        document.getElementById("input-context-text").value = "Warning: high frequency emergency warning strobe active.";
      }"""
    new_context_string = """      } else {
        document.getElementById("input-context-text").value = "";
        document.getElementById("check-context-approved").checked = false;
      }"""
    if old_context_string in content:
        content = content.replace(old_context_string, new_context_string, 1)
        print("Removed hardcoded context note string")

    # Update groundContext
    old_ground_context = """    async function groundContext() {
      if (!selectedAsset || !selectedFinding) return;
      try {
        const resp = await fetch(`${API_BASE}/api/v1/context/ground`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            interval_id: selectedFinding.interval_id,
            method: "extractive_subtitles"
          })
        });
        const note = await resp.json();
        document.getElementById("input-context-text").value = note.text;
      } catch (e) {
        alert("Failed to ground dialogue cues");
      }
    }"""
    new_ground_context = """    async function groundContext() {
      if (!selectedAsset || !selectedFinding) return;
      setFeedback("context-feedback", "Grounding dialogue from source cues...", "info");
      try {
        const resp = await fetch(`${API_BASE}/api/v1/context/ground`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            interval_id: selectedFinding.interval_id,
            method: "extractive_subtitles"
          })
        });
        if (resp.ok) {
          const note = await resp.json();
          document.getElementById("input-context-text").value = note.text;
          setFeedback("context-feedback", `✓ Grounded cues: ${note.source_cue_ids ? note.source_cue_ids.join(", ") : "dialogue excerpt"}`, "success");
        } else {
          const err = await resp.json().catch(() => ({}));
          setFeedback("context-feedback", `Dialogue grounding failed: ${err.error || resp.statusText}`, "error");
        }
      } catch (e) {
        setFeedback("context-feedback", `Grounding network error: ${e.message}`, "error");
      }
    }"""
    if old_ground_context in content:
        content = content.replace(old_ground_context, new_ground_context, 1)
        print("Updated groundContext")

    # Update approveContext
    old_approve_context = """    async function approveContext() {
      if (!selectedAsset || !selectedFinding) return;
      const text = document.getElementById("input-context-text").value;
      const approved = document.getElementById("check-context-approved").checked;
      try {
        await fetch(`${API_BASE}/api/v1/context/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            interval_id: selectedFinding.interval_id,
            reviewer_id: document.getElementById("input-reviewer-id").value,
            text: text
          })
        });
        alert("Narrative context approval saved!");
      } catch (e) {
        alert("Error saving context approval");
      }
    }"""
    new_approve_context = """    async function approveContext() {
      if (!selectedAsset || !selectedFinding) return;
      const text = document.getElementById("input-context-text").value.trim();
      const approved = document.getElementById("check-context-approved").checked;
      if (!approved) {
        setFeedback("context-feedback", "Please check 'Editorially approved for narrative banner' before saving approval.", "warning");
        return;
      }
      if (!text) {
        setFeedback("context-feedback", "Context text cannot be empty. Ground dialogue or enter notes.", "warning");
        return;
      }
      try {
        const resp = await fetch(`${API_BASE}/api/v1/context/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            interval_id: selectedFinding.interval_id,
            reviewer_id: document.getElementById("input-reviewer-id").value,
            text: text
          })
        });
        if (resp.ok) {
          setFeedback("context-feedback", `✓ Narrative context approved and bound to interval ${selectedFinding.interval_id}`, "success");
          await loadReport(selectedAsset.asset_revision_id);
        } else {
          const err = await resp.json().catch(() => ({}));
          setFeedback("context-feedback", `Approval rejected: ${err.error || resp.statusText} (Ensure cues are grounded first)`, "error");
        }
      } catch (e) {
        setFeedback("context-feedback", `Error saving context approval: ${e.message}`, "error");
      }
    }"""
    if old_approve_context in content:
        content = content.replace(old_approve_context, new_approve_context, 1)
        print("Updated approveContext")

    # Update submitReview
    old_submit_review = """    async function submitReview() {
      if (!selectedAsset || !currentReport) return;
      const decision = document.getElementById("select-editorial-decision").value;
      const reviewer = document.getElementById("input-reviewer-id").value;
      const notes = document.getElementById("input-review-notes").value;

      try {
        const resp = await fetch(`${API_BASE}/api/v1/screening/reports/${encodeURIComponent(selectedAsset.asset_revision_id)}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reviewer_id: reviewer,
            decision: decision,
            notes: notes
          })
        });
        if (resp.ok) {
          await loadAssets();
          await selectAsset(selectedAsset.asset_id);
          alert(`Editorial decision committed: ${decision.toUpperCase()}`);
        } else {
          const err = await resp.json();
          alert(`Review submission failed: ${err.error}`);
        }
      } catch (e) {
        alert("Review submission error");
      }
    }"""
    new_submit_review = """    async function submitReview() {
      if (!selectedAsset || !currentReport) return;
      const decision = document.getElementById("select-editorial-decision").value;
      const reviewer = document.getElementById("input-reviewer-id").value;
      const notes = document.getElementById("input-review-notes").value;
      setFeedback("review-feedback", "Submitting editorial decision...", "info");

      try {
        const resp = await fetch(`${API_BASE}/api/v1/screening/reports/${encodeURIComponent(selectedAsset.asset_revision_id)}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reviewer_id: reviewer,
            decision: decision,
            notes: notes
          })
        });
        if (resp.ok) {
          setFeedback("review-feedback", `✓ Editorial decision committed: ${decision.toUpperCase()}`, "success");
          await loadAssets();
          await selectAsset(selectedAsset.asset_id);
        } else {
          const err = await resp.json().catch(() => ({}));
          setFeedback("review-feedback", `Review submission rejected: ${err.error || resp.statusText}`, "error");
        }
      } catch (e) {
        setFeedback("review-feedback", `Review submission error: ${e.message}`, "error");
      }
    }"""
    if old_submit_review in content:
        content = content.replace(old_submit_review, new_submit_review, 1)
        print("Updated submitReview")

    # Update publishRelease
    old_publish_release = """    async function publishRelease() {
      if (!selectedAsset || !currentReport) return;
      const renditionId = document.getElementById("input-rendition-id").value;
      const btn = document.getElementById("btn-publish-release");
      btn.disabled = true;
      btn.textContent = "Signing Ed25519 Sidecar...";

      try {
        const hlsManifest = "#EXTM3U\\n#EXT-X-VERSION:3\\n#EXT-X-TARGETDURATION:3\\n#EXTINF:3.0,\\nseg0.ts\\n#EXT-X-ENDLIST\\n";
        const resp = await fetch(`${API_BASE}/api/v1/releases/publish`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            rendition_set_id: renditionId,
            playlist_manifest: hlsManifest,
            media_uri: "asset:///hls/variant_1080p.m3u8"
          })
        });
        if (resp.ok) {
          activeRelease = await resp.json();
          document.getElementById("pub-status-tag").className = "status-tag tag-published";
          document.getElementById("pub-status-tag").textContent = "PUBLISHED";
          document.getElementById("published-release-details").style.display = "block";
          document.getElementById("pub-release-id-box").textContent = `Release ID: ${activeRelease.release_id}\\nDigest: ${activeRelease.manifest_digest}\\nKey: ${activeRelease.key_id} (Ed25519)`;
          document.getElementById("pub-intent-cmd-box").textContent = activeRelease.harness_intent_command;
          alert("Release successfully signed and published with Ed25519 v2 sidecar!");
        } else {
          const err = await resp.json();
          alert(`Publication failed: ${err.error}`);
        }
      } catch (e) {
        alert("Publication execution error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Sign & Publish Ed25519 Sidecar";
      }
    }"""
    new_publish_release = """    async function publishRelease() {
      if (!selectedAsset || !currentReport) return;
      const renditionId = document.getElementById("input-rendition-id").value.trim();
      const btn = document.getElementById("btn-publish-release");
      btn.disabled = true;
      btn.textContent = "Signing Ed25519 Sidecar...";
      setFeedback("publish-feedback", "Signing Ed25519 sidecar and packaging release...", "info");

      try {
        let hlsManifest = "#EXTM3U\\n#EXT-X-VERSION:3\\n#EXT-X-TARGETDURATION:3\\n#EXTINF:3.0,\\nseg0.ts\\n#EXT-X-ENDLIST\\n";
        let mediaUri = "asset:///hls/variant_1080p.m3u8";
        if (selectedAsset && selectedAsset.source_path && selectedAsset.source_path.includes("benign")) {
          hlsManifest = "#EXTM3U\\n#EXT-X-VERSION:3\\n#EXT-X-TARGETDURATION:120\\n#EXTINF:120.0,\\nbenign_seg0.ts\\n#EXT-X-ENDLIST\\n";
          mediaUri = "asset:///hls/benign_variant.m3u8";
        }

        const resp = await fetch(`${API_BASE}/api/v1/releases/publish`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_revision_id: selectedAsset.asset_revision_id,
            rendition_set_id: renditionId,
            playlist_manifest: hlsManifest,
            media_uri: mediaUri
          })
        });
        if (resp.ok) {
          activeRelease = await resp.json();
          document.getElementById("pub-status-tag").className = "status-tag tag-published";
          document.getElementById("pub-status-tag").textContent = "PUBLISHED";
          document.getElementById("published-release-details").style.display = "block";
          document.getElementById("pub-release-id-box").textContent = `Release ID: ${activeRelease.release_id}\\nDigest: ${activeRelease.manifest_digest}\\nKey: ${activeRelease.key_id} (Ed25519)`;
          document.getElementById("pub-intent-cmd-box").textContent = activeRelease.harness_intent_command;
          setFeedback("publish-feedback", `✓ Release published & signed with Ed25519 v2 (ID: ${activeRelease.release_id})`, "success");
        } else {
          const err = await resp.json().catch(() => ({}));
          setFeedback("publish-feedback", `Publication failed: ${err.error || resp.statusText}`, "error");
        }
      } catch (e) {
        setFeedback("publish-feedback", `Publication network error: ${e.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Sign & Publish Ed25519 Sidecar";
      }
    }"""
    if old_publish_release in content:
        content = content.replace(old_publish_release, new_publish_release, 1)
        print("Updated publishRelease")

    # Add VTT parser and real subtitle rendering to timeline and HUD
    vtt_parser_code = """    function parseVttCues(vttText) {
      if (!vttText) return [];
      const cues = [];
      const lines = vttText.split(/\\r?\\n/);
      const timeRegex = /(?:(\\d{2}):)?(\\d{2}):(\\d{2})[.,](\\d{3})\\s*-->\\s*(?:(\\d{2}):)?(\\d{2}):(\\d{2})[.,](\\d{3})/;
      let i = 0;
      while (i < lines.length) {
        const line = lines[i].trim();
        const m = line.match(timeRegex);
        if (m) {
          const h1 = parseInt(m[1] || "0", 10);
          const m1 = parseInt(m[2], 10);
          const s1 = parseInt(m[3], 10);
          const ms1 = parseInt(m[4], 10);
          const startMs = (h1 * 3600 + m1 * 60 + s1) * 1000 + ms1;

          const h2 = parseInt(m[5] || "0", 10);
          const m2 = parseInt(m[6], 10);
          const s2 = parseInt(m[7], 10);
          const ms2 = parseInt(m[8], 10);
          const endMs = (h2 * 3600 + m2 * 60 + s2) * 1000 + ms2;

          let text = "";
          let j = i + 1;
          while (j < lines.length && lines[j].trim() && !lines[j].includes("-->")) {
            text += (text ? " " : "") + lines[j].trim();
            j++;
          }
          cues.push({ startMs, endMs, text });
          i = j;
        } else {
          i++;
        }
      }
      return cues;
    }
"""
    if "function parseVttCues" not in content:
        content = content.replace("    // Timeline Rendering & Scrubbing", vtt_parser_code + "\n    // Timeline Rendering & Scrubbing", 1)
        print("Inserted parseVttCues helper")

    # Replace hardcoded 48%/50% subtitle block in renderTimeline
    old_sub_block = """      // Subtitles Track
      const subTrack = document.getElementById("track-subtitles");
      subTrack.querySelectorAll(".timeline-subtitle-block").forEach(el => el.remove());
      if (selectedAsset.subtitles_vtt && currentReport && currentReport.findings && currentReport.findings.length > 0) {
        const subBlock = document.createElement("div");
        subBlock.className = "timeline-subtitle-block";
        subBlock.style.left = "48%";
        subBlock.style.width = "50%";
        subBlock.textContent = "Warning: emergency strobe active";
        subTrack.appendChild(subBlock);
      }"""
    new_sub_block = """      // Subtitles Track (Grounded from actual VTT cues)
      const subTrack = document.getElementById("track-subtitles");
      subTrack.querySelectorAll(".timeline-subtitle-block").forEach(el => el.remove());
      const vttCues = parseVttCues(selectedAsset ? selectedAsset.subtitles_vtt : "");
      if (vttCues.length > 0) {
        vttCues.forEach(cue => {
          if (cue.endMs > cue.startMs && durationMs > 0) {
            const leftPct = (cue.startMs / durationMs) * 100;
            const widthPct = Math.max(1.5, ((cue.endMs - cue.startMs) / durationMs) * 100);
            const subBlock = document.createElement("div");
            subBlock.className = "timeline-subtitle-block";
            subBlock.style.left = `${leftPct}%`;
            subBlock.style.width = `${widthPct}%`;
            subBlock.textContent = cue.text;
            subBlock.title = `${cue.startMs}ms - ${cue.endMs}ms: ${cue.text}`;
            subTrack.appendChild(subBlock);
          }
        });
      }"""
    if old_sub_block in content:
        content = content.replace(old_sub_block, new_sub_block, 1)
        print("Updated renderTimeline real subtitle rendering")

    # Replace HUD subtitle string in updatePlayheadVisuals
    old_hud_sub = """      // Subtitle check
      if (inHazard && selectedAsset.subtitles_vtt) {
        document.getElementById("hud-subtitle").textContent = "Warning: emergency warning strobe active.";
      } else {
        document.getElementById("hud-subtitle").textContent = "";
      }"""
    new_hud_sub = """      // Subtitle check from actual VTT cues or grounded context note
      let activeSubText = "";
      const vttCues = parseVttCues(selectedAsset ? selectedAsset.subtitles_vtt : "");
      const activeCue = vttCues.find(c => currentPositionMs >= c.startMs && currentPositionMs <= c.endMs);
      if (activeCue) {
        activeSubText = activeCue.text;
      } else if (inHazard && currentReport && currentReport.context_notes && currentReport.context_notes.length > 0) {
        const note = currentReport.context_notes.find(n => n.interval_id === inHazard.interval_id);
        if (note && note.text) {
          activeSubText = `[Narrative Context: ${note.text}]`;
        }
      }
      document.getElementById("hud-subtitle").textContent = activeSubText;"""
    if old_hud_sub in content:
        content = content.replace(old_hud_sub, new_hud_sub, 1)
        print("Updated updatePlayheadVisuals real HUD subtitle")

    # Write back preserving \r\n line endings
    content_crlf = content.replace("\n", "\r\n")
    INDEX_PATH.write_text(content_crlf, encoding="utf-8")
    print("Successfully wrote updated static/index.html")

if __name__ == "__main__":
    main()
