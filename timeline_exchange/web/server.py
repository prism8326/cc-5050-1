"""Zero-dependency HTTP server driving the exchange kernel.

Endpoints
---------
GET  /                        static single-page UI
GET  /api/formats             supported formats and transition tables
POST /api/exchange            {document, source_format, target_format, rate?}
                              -> export text, loss report, re-read verification,
                                 track structure, per-frame selections
GET  /api/frame?session=&t=   exact record-time frame selection + live SVG tile
POST /api/load-sample         load the bundled mixed-rate test project
GET  /media/<path>            frame-bundle PNG access for the SVG tiles

All state stays server-side in a session; UI controls call the kernel and the
kernel's failure/loss data is returned to the UI rather than being swallowed.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tlx import tl_json, cmx, exchange, preview, render
from tlx.timecode import FrameRate, frame_time, frame_index_at, format_timecode
from tlx.model import Timeline
from tlx.loss import LossReport

WEB = os.path.join(ROOT, "web")
PROJECT = os.path.join(ROOT, "test_project")

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _timeline_structure(tl: Timeline) -> dict:
    origin = tl.start_time
    rate = tl.rate
    return {
        "timeline_id": tl.timeline_id,
        "rate": str(rate),
        "start_timecode": format_timecode(origin, rate),
        "duration_rational": str(tl.duration),
        "duration_timecode": format_timecode(tl.duration - origin, rate),
        "record_frame_count": frame_index_at(tl.duration - origin, rate),
        "sources": {
            sid: {
                "path": s.path,
                "rate": str(s.rate),
                "exists": s.exists,
                "duration_rational": str(s.duration),
                "reel": s.reel,
            }
            for sid, s in tl.sources.items()
        },
        "tracks": [
            {
                "track_id": t.track_id,
                "kind": t.kind,
                "index": t.index,
                "items": [
                    {
                        "item": "clip",
                        "clip_id": c.clip_id,
                        "source_id": c.source_id,
                        "start": format_timecode(c.timeline_start - origin, rate),
                        "end": format_timecode(c.timeline_end - origin, rate),
                        "start_rational": str(c.timeline_start),
                        "duration_rational": str(c.duration),
                        "source_in": format_timecode(
                            c.source_in, tl.sources[c.source_id].rate
                        ),
                        "source_out": format_timecode(
                            c.source_out, tl.sources[c.source_id].rate
                        ),
                        "transition_in": c.transition_in,
                        "transition_duration_frames": int(
                            c.transition_duration * rate.rate
                        ),
                        "enabled": c.enabled,
                    }
                    for c in t.clips
                ]
                + [
                    {
                        "item": "gap",
                        "gap_id": g.gap_id,
                        "start": format_timecode(g.start - origin, rate),
                        "end": format_timecode(g.end - origin, rate),
                        "duration_rational": str(g.duration),
                    }
                    for g in t.gaps
                ],
            }
            for t in tl.tracks
        ],
    }


def _frame_table(tl: Timeline, limit: int | None = None) -> list[dict]:
    count = frame_index_at(tl.duration - tl.start_time, tl.rate)
    if limit is not None:
        count = min(count, limit)
    rows = []
    for i in range(count):
        t = tl.start_time + frame_time(i, tl.rate)
        sel = preview.select_frame(tl, t)
        rows.append(preview.describe_selection(sel, tl))
    return rows


def _create_session(result: exchange.ExchangeResult, base_dir: str) -> str:
    sid = uuid.uuid4().hex
    with _LOCK:
        _SESSIONS[sid] = {
            "original": result.original,
            "reimported": result.reimported,
            "exported": result.exported_text,
            "loss": result.loss_report,
            "missing": result.missing_sources,
            "base_dir": base_dir,
            "mismatches": result.mismatches,
            "compared": result.compared_frames,
            "source_format": result.source_format,
            "target_format": result.target_format,
        }
    return sid


def _session_payload(sid: str) -> dict:
    s = _SESSIONS[sid]
    orig, reimp = s["original"], s["reimported"]
    return {
        "session": sid,
        "source_format": s["source_format"],
        "target_format": s["target_format"],
        "exported_document": s["exported"],
        "loss_report": s["loss"].to_jsonable(orig.rate),
        "verification": {
            "compared_frames": s["compared"],
            "mismatch_count": len(s["mismatches"]),
            "mismatches": [m.to_jsonable(orig.rate) for m in s["mismatches"]],
            "missing_sources": s["missing"],
        },
        "before": _timeline_structure(orig),
        "after": _timeline_structure(reimp),
        "frame_table": _frame_table(orig, limit=120),
        "record_rate": str(orig.rate),
        "record_frame_count": frame_index_at(
            orig.duration - orig.start_time, orig.rate
        ),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TLX/1.0"

    def log_message(self, fmt, *args):  # quiet by default
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, code, message):
        self._send_json({"error_code": code, "error": message}, status=status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = "static/" + path.split("/static/", 1)[1]
            ctype = "application/javascript; charset=utf-8" if name.endswith(".js") \
                else "text/css; charset=utf-8"
            return self._serve_static(name, ctype)
        if path == "/api/formats":
            from tlx.model import SUPPORTED_TRANSITIONS, CMX_EXPRESSIBLE_TRANSITIONS
            return self._send_json({
                "formats": [
                    {"id": tl_json.FORMAT_NAME, "label": "TL-JSON (rich, lossless)"},
                    {"id": cmx.FORMAT_NAME, "label": "CMX 3600 EDL (single V track)"},
                ],
                "internal_transitions": sorted(SUPPORTED_TRANSITIONS),
                "cmx_expressible_transitions": sorted(CMX_EXPRESSIBLE_TRANSITIONS),
                "gap_semantics": "gaps are explicit model objects; CMX export "
                                 "reports them because EDL has no gap construct",
            })
        if path == "/api/frame":
            return self._handle_frame()
        if path.startswith("/media/"):
            return self._serve_media(path[len("/media/"):])
        self._send_error_json(404, "not_found", f"unknown path {path}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send_error_json(400, "bad_json", str(exc))
        if path == "/api/exchange":
            return self._handle_exchange(payload)
        if path == "/api/load-sample":
            return self._handle_load_sample()
        self._send_error_json(404, "not_found", f"unknown path {path}")

    def _handle_exchange(self, payload):
        doc = payload.get("document", "")
        src_fmt = payload.get("source_format")
        tgt_fmt = payload.get("target_format")
        if src_fmt not in exchange.FORMATS or tgt_fmt not in exchange.FORMATS:
            return self._send_error_json(
                400, "bad_format",
                f"formats must be one of {exchange.FORMATS}",
            )
        rate = None
        if payload.get("rate"):
            try:
                rate = FrameRate.from_value(payload["rate"])
            except (ValueError, TypeError) as exc:
                return self._send_error_json(400, "bad_rate", str(exc))
        try:
            sources = None
            if payload.get("source_registry_document"):
                sources = dict(
                    tl_json.import_text(
                        payload["source_registry_document"]
                    ).sources
                )
            result = exchange.run_exchange(
                doc, src_fmt, tgt_fmt, rate=rate, sources=sources,
                media_base_dir=payload.get("media_base_dir") or PROJECT,
            )
        except Exception as exc:  # kernel failure -> UI, not a 500 crash
            return self._send_error_json(422, "exchange_failed", f"{type(exc).__name__}: {exc}")
        sid = _create_session(result, payload.get("media_base_dir") or PROJECT)
        self._send_json(_session_payload(sid))

    def _handle_load_sample(self):
        path = os.path.join(PROJECT, "project.tl.json")
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
        try:
            result = exchange.run_exchange(
                doc, tl_json.FORMAT_NAME, cmx.FORMAT_NAME,
                media_base_dir=PROJECT,
            )
        except Exception as exc:
            return self._send_error_json(422, "exchange_failed", str(exc))
        sid = _create_session(result, PROJECT)
        payload = _session_payload(sid)
        payload["sample_document"] = doc
        self._send_json(payload)

    def _handle_frame(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        sid = q.get("session", [""])[0]
        stage = q.get("stage", ["before"])[0]
        with _LOCK:
            sess = _SESSIONS.get(sid)
        if not sess:
            return self._send_error_json(404, "no_session", "session expired; rerun exchange")
        tl = sess["original"] if stage == "before" else sess["reimported"]
        try:
            frame_idx = int(q.get("frame", ["0"])[0])
        except ValueError:
            return self._send_error_json(400, "bad_frame", "frame must be an integer")
        max_idx = frame_index_at(tl.duration - tl.start_time, tl.rate) - 1
        frame_idx = max(0, min(frame_idx, max_idx))
        t = tl.start_time + frame_time(frame_idx, tl.rate)
        sel = preview.select_frame(tl, t)
        svg = render.render_tile_svg(tl, t, sess["base_dir"])
        self._send_json({
            "frame_index": frame_idx,
            "selection": preview.describe_selection(sel, tl),
            "svg": svg,
            "stage": stage,
        })

    def _serve_media(self, rel: str):
        # Only allow files inside the project media root.
        base = os.path.realpath(PROJECT)
        target = os.path.realpath(os.path.join(base, rel))
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            return self._send_error_json(404, "media_not_found", rel)
        ctype = "image/png" if target.endswith(".png") else "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str, ctype: str):
        target = os.path.realpath(os.path.join(WEB, name))
        if not target.startswith(os.path.realpath(WEB) + os.sep):
            return self._send_error_json(403, "forbidden", name)
        if not os.path.isfile(target):
            return self._send_error_json(404, "not_found", name)
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"clip exchange UI: http://{host}:{port}/")
    print(f"project media root: {PROJECT}")
    httpd.serve_forever()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    serve(args.host, args.port)
