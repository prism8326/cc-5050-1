"use strict";

const $ = (id) => document.getElementById(id);
const state = { session: null, stage: "before", frameCount: 0, frameTable: [] };
let frameReqSeq = 0;

function setStatus(el, kind, html) {
  el.className = `status ${kind}`;
  el.innerHTML = html;
}

function clearStatus(el) {
  el.className = "status";
  el.innerHTML = "";
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    const err = new Error(data.error || "request failed");
    err.code = data.error_code;
    throw err;
  }
  return data;
}

function renderLosses(report) {
  const host = $("lossList");
  if (!report.entries.length) {
    host.innerHTML = '<p class="badge-ok">无损失：目标格式可完整表达全部内容。</p>';
    return;
  }
  host.innerHTML = report.entries.map((e) => {
    const pos = e.timeline_start && e.timeline_start.timecode
      ? `位置 ${e.timeline_start.timecode}`
        + (e.timeline_end && e.timeline_end.timecode ? ` – ${e.timeline_end.timecode}` : "")
      : "无单一时间位置";
    return `<div class="loss-row loss-${e.severity}">
      <strong class="badge-${e.severity === "error" ? "err" : "ok"}">${e.severity.toUpperCase()}</strong>
      <code>${e.code}</code> · ${e.kind} <strong>${e.object_id}</strong><br>
      <span class="muted">${pos}</span><br>${e.message}
    </div>`;
  }).join("");
}

function renderTimeline(elId, struct) {
  const el = $(elId);
  const total = struct.record_frame_count || 1;
  const pct = (tc) => {
    // tc is HH:MM:SS:FF at record rate; convert via frame count ratio.
    const parts = tc.split(":").map(Number);
    const nominal = Number(struct.rate.match(/^\d+/)?.[0] || 24);
    const frames = ((parts[0] * 60 + parts[1]) * 60 + parts[2]) * nominal + parts[3];
    return (frames / total) * 100;
  };
  let html = `<div class="ruler">`;
  for (let q = 0; q <= 4; q++) {
    const f = Math.round((total * q) / 4);
    const nominal = Number(struct.rate.match(/^\d+/)?.[0] || 24);
    const hh = String(Math.floor(f / nominal / 3600)).padStart(2, "0");
    const mm = String(Math.floor(f / nominal / 60) % 60).padStart(2, "0");
    const ss = String(Math.floor(f / nominal) % 60).padStart(2, "0");
    const ff = String(f % nominal).padStart(2, "0");
    html += `<span style="left:${q * 25}%">${hh}:${mm}:${ss}:${ff}</span>`;
  }
  html += `</div>`;

  for (const track of struct.tracks) {
    const sorted = track.items.slice().sort((a, b) => a.start.localeCompare(b.start));
    html += `<div class="track-row">
      <div class="track-label">${track.track_id}<br>${track.kind}</div>
      <div class="track-lane">`;
    for (const it of sorted) {
      const left = pct(it.start);
      const width = Math.max(0.5, pct(it.end) - pct(it.start));
      if (it.item === "gap") {
        html += `<div class="gap-block" style="left:${left}%;width:${width}%"
          title="空隙 ${it.gap_id} ${it.start}→${it.end}">GAP ${it.gap_id}</div>`;
      } else {
        const trans = it.transition_in && it.transition_in !== "cut"
          ? ` · ${it.transition_in}${it.transition_duration_frames}f` : "";
        html += `<div class="clip-block ${trans ? "trans" : ""}"
          style="left:${left}%;width:${width}%"
          title="${it.clip_id} 源${it.source_id} 源入${it.source_in} 源出${it.source_out}${trans}">
          ${it.clip_id} · ${it.source_id}${trans}</div>`;
      }
    }
    html += `<div class="cursor-line" id="${elId}-cursor" style="left:0%"></div>`;
    html += `</div></div>`;
  }
  el.innerHTML = html;
}

function renderFrameTable(rows) {
  const tb = $("frameTable").querySelector("tbody");
  tb.innerHTML = rows.map((r) => `<tr>
    <td class="mono">f${$("scrub").value && false ? "" : ""}${r.record_timecode}</td>
    <td><span class="pill ${r.kind}">${r.kind}</span></td>
    <td class="mono">${r.clip_id || "—"}</td>
    <td class="mono">${r.source_id || "—"}</td>
    <td class="mono">${r.source_frame_index ?? "—"}</td>
    <td class="mono">${r.source_timecode || "—"}</td>
    <td class="mono">${r.transition_in || "—"}</td>
  </tr>`).join("");
}

function applyResult(data) {
  state.session = data.session;
  state.frameCount = data.record_frame_count;
  state.frameTable = data.frame_table;

  $("resultsCard").style.display = "block";
  $("timelineCard").style.display = "block";
  $("previewCard").style.display = "block";
  $("exportDoc").value = data.exported_document;

  const v = data.verification;
  const loss = data.loss_report;
  const missing = v.missing_sources.length
    ? `<br><span class="badge-err">缺失素材引用：${v.missing_sources.join(", ")}（预览将显示定位诊断板，不使用占位图）</span>`
    : '<br><span class="badge-ok">全部素材引用可定位</span>';
  setStatus(
    $("verifyStatus"),
    loss.has_errors ? "fail" : "ok",
    `重新读取逐帧比对：共 ${v.compared_frames} 个记录帧，差异 ${v.mismatch_count} 处；`
    + `损失报告 ${loss.entry_count} 条（error ${loss.entries.filter(e => e.severity === "error").length} / `
    + `warning ${loss.entries.filter(e => e.severity === "warning").length}）。${missing}`
  );
  renderLosses(loss);

  $("beforeMeta").textContent = `· ${data.source_format} · ${data.before.duration_timecode} · ${data.before.record_frame_count}f`;
  $("afterMeta").textContent = `· ${data.target_format} · ${data.after.duration_timecode} · ${data.after.record_frame_count}f`;
  renderTimeline("beforeTl", data.before);
  renderTimeline("afterTl", data.after);
  renderFrameTable(data.frame_table);

  $("stageRate").textContent = `记录帧率 ${data.record_rate}`;
  $("scrub").max = String(Math.max(0, state.frameCount - 1));
  $("scrub").value = "0";
  setStage("before");
  requestFrame(0);
}

async function runExchange(sample) {
  clearStatus($("inputStatus"));
  $("runBtn").disabled = true;
  try {
    const data = sample
      ? await postJSON("/api/load-sample", {})
      : await postJSON("/api/exchange", {
          document: $("docInput").value,
          source_format: $("srcFmt").value,
          target_format: $("tgtFmt").value,
          rate: $("rate").value.trim(),
        });
    if (sample && data.sample_document) $("docInput").value = data.sample_document;
    applyResult(data);
    setStatus($("inputStatus"), "ok",
      sample ? "示例工程已载入并完成交换。" : "交换成功，已重新读取并逐帧校验。");
  } catch (err) {
    setStatus($("inputStatus"), "fail",
      `交换失败（已回传剪辑交换内核）：<strong>${err.code || "error"}</strong> — ${err.message}`);
  } finally {
    $("runBtn").disabled = false;
  }
}

async function requestFrame(frameIdx) {
  if (!state.session) return;
  const seq = ++frameReqSeq;
  try {
    const q = new URLSearchParams({
      session: state.session, stage: state.stage, frame: String(frameIdx),
    });
    const res = await fetch(`/api/frame?${q}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    // A newer scrub/stage request supersedes this response.
    if (seq !== frameReqSeq) return;
    $("tile").innerHTML = data.svg;
    const sel = data.selection;
    $("curTc").textContent = sel.record_timecode;
    $("curFrame").textContent = `f${data.frame_index}`;
    const lines = [
      `类型 ${sel.kind}`,
      sel.clip_id ? `片段 ${sel.clip_id}` : null,
      sel.source_id ? `源 ${sel.source_id}（${sel.source_rate}）` : null,
      sel.source_frame_index !== null && sel.source_frame_index !== undefined
        ? `源帧 #${sel.source_frame_index} @ ${sel.source_timecode}（${sel.source_time_rational}s）`
        : null,
      sel.transition_in ? `转场 ${sel.transition_in} 进度 ${sel.transition_progress_rational}` : null,
      sel.detail || null,
    ].filter(Boolean);
    $("selDetail").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");

    const cursorId = state.stage === "before" ? "beforeTl-cursor" : "afterTl-cursor";
    const cursor = $(cursorId);
    if (cursor) {
      const max = Math.max(1, state.frameCount - 1);
      cursor.style.left = `${(data.frame_index / max) * 100}%`;
    }
  } catch (err) {
    $("tile").innerHTML =
      `<div style="padding:20px;color:var(--err)">预览失败：${err.message}</div>`;
  }
}

function setStage(stage) {
  state.stage = stage;
  document.querySelectorAll(".stage-switch button").forEach((b) => {
    b.classList.toggle("active", b.dataset.stage === stage);
  });
  requestFrame(Number($("scrub").value));
}

$("runBtn").addEventListener("click", () => runExchange(false));
$("sampleBtn").addEventListener("click", () => runExchange(true));
$("scrub").addEventListener("input", (e) => requestFrame(Number(e.target.value)));
document.querySelectorAll(".stage-switch button").forEach((b) => {
  b.addEventListener("click", () => setStage(b.dataset.stage));
});
