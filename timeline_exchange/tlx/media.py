"""Media catalog: the ground truth a source reference resolves against.

A timeline's ``source_id`` is a *display name*.  Two different projects may use
the same display name for different media (different content, different source
timecode origin).  Resolution therefore follows the stable ``media_ref`` and
verifies ``content_id`` and ``timecode_origin`` afterwards.  Name-only lookup
is deliberately available but never used automatically: it is recorded as an
ambiguity so a validator can refuse the "it plays, so it is fine" shortcut.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .timecode import FrameRate
from .model import Source, Timeline


class MediaCatalogError(ValueError):
    pass


def bundle_content_id(bundle_dir: str) -> str:
    """Fingerprint a frame bundle from the actual pixel bytes on disk."""
    with open(os.path.join(bundle_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    digest = hashlib.sha256()
    for name in manifest["frames"]:
        with open(os.path.join(bundle_dir, name), "rb") as fh:
            digest.update(fh.read())
    return "sha256:" + digest.hexdigest()[:24]


@dataclass
class MediaAsset:
    path: str
    media_ref: str
    content_id: str
    rate: FrameRate
    duration: Fraction
    timecode_origin: Fraction = Fraction(0)
    display_name: Optional[str] = None
    frame_count: int = 0

    @property
    def identity(self) -> tuple:
        return (self.media_ref, self.content_id, self.timecode_origin)

    def to_jsonable(self) -> dict:
        return {
            "path": self.path,
            "media_ref": self.media_ref,
            "content_id": self.content_id,
            "rate": f"{self.rate.numerator}/{self.rate.denominator}",
            "duration_rational": str(self.duration),
            "timecode_origin_rational": str(self.timecode_origin),
            "timecode_origin": self.timecode_origin_label(),
            "display_name": self.display_name,
            "frame_count": self.frame_count,
        }

    def timecode_origin_label(self) -> str:
        from .timecode import format_timecode
        return format_timecode(self.timecode_origin, self.rate)

    @classmethod
    def from_bundle(cls, bundle_dir: str, media_ref: str,
                    display_name: str | None = None,
                    timecode_origin: Fraction | None = None) -> "MediaAsset":
        manifest_path = os.path.join(bundle_dir, "manifest.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        rate = FrameRate.from_value(manifest["rate"])
        origin = (
            Fraction(manifest.get("timecode_origin_rational", "0"))
            if timecode_origin is None
            else timecode_origin
        )
        content_id = manifest.get("content_id") or bundle_content_id(bundle_dir)
        return cls(
            path=bundle_dir,
            media_ref=media_ref,
            content_id=content_id,
            rate=rate,
            duration=Fraction(manifest["duration_rational"]),
            timecode_origin=origin,
            display_name=display_name or manifest.get("source_id"),
            frame_count=int(manifest.get("frame_count", len(manifest["frames"]))),
        )


@dataclass
class MediaCatalog:
    assets: list[MediaAsset] = field(default_factory=list)
    _by_ref: dict[str, MediaAsset] = field(default_factory=dict, init=False)
    _by_name: dict[str, list[MediaAsset]] = field(default_factory=dict, init=False)

    def add(self, asset: MediaAsset) -> None:
        if asset.media_ref in self._by_ref:
            raise MediaCatalogError(
                f"duplicate media_ref {asset.media_ref!r}; references must be unique"
            )
        self.assets.append(asset)
        self._by_ref[asset.media_ref] = asset
        if asset.display_name:
            self._by_name.setdefault(asset.display_name, []).append(asset)

    def by_ref(self, media_ref: str | None) -> Optional[MediaAsset]:
        if not media_ref:
            return None
        return self._by_ref.get(media_ref)

    def by_name(self, name: str | None) -> list[MediaAsset]:
        """All assets whose display name matches.  Ambiguous by construction."""
        if not name:
            return []
        return list(self._by_name.get(name, []))

    def name_only_is_ambiguous(self, name: str | None,
                               expected_ref: str | None) -> bool:
        matches = self.by_name(name)
        return len(matches) > 0 and not (
            len(matches) == 1 and matches[0].media_ref == expected_ref
        )

    @classmethod
    def from_directories(cls, roots: list[str]) -> "MediaCatalog":
        """Build a catalog from a simple JSON index if present, else bundles.

        A ``catalog.json`` in a root lists ``{"assets": [...]}`` with explicit
        ``media_ref`` and optional ``display_name`` / ``timecode_origin``.
        Directory scanning is not used because a media file's location is not
        its identity.
        """
        catalog = cls()
        for root in roots:
            index = os.path.join(root, "catalog.json")
            if not os.path.exists(index):
                continue
            with open(index, encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data.get("assets", []):
                bundle = row["path"]
                if not os.path.isabs(bundle):
                    bundle = os.path.join(os.path.dirname(index), bundle)
                catalog.add(
                    MediaAsset.from_bundle(
                        bundle,
                        media_ref=row["media_ref"],
                        display_name=row.get("display_name"),
                        timecode_origin=(
                            Fraction(row["timecode_origin_rational"])
                            if row.get("timecode_origin_rational") is not None
                            else None
                        ),
                    )
                )
        return catalog

    def to_jsonable(self) -> dict:
        return {"assets": [a.to_jsonable() for a in self.assets]}


def source_identity(src: Source) -> dict:
    return {
        "source_id": src.source_id,
        "media_ref": src.media_ref,
        "content_id": src.content_id,
        "timecode_origin_rational": str(src.timecode_origin),
        "reel": src.reel,
    }


def timeline_media_summary(tl: Timeline) -> list[dict]:
    return [source_identity(s) for s in tl.sources.values()]
