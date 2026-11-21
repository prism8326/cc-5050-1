"""Target-format capability manifest.

A capability is a single semantic feature the exchange can preserve or must
degrade.  The manifest is data, not prose: exporters, the media relinker and
the contract runner all consult the same :class:`TargetProfile`, so "the format
supports X" can never diverge from what the tool actually does.

Profiles can be *narrowed* (``narrow(profile, "transition:wipe")``) to model a
target that is a strict subset of an existing one.  Contract samples are then
re-run against the narrowed profile; only the samples that use the removed
capability change outcome, and each reports the first record position where
edit semantics is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .model import (
    SUPPORTED_TRANSITIONS,
    TRANSITION_CUT,
    TRANSITION_DISSOLVE,
    TRANSITION_FADE,
    TRANSITION_WIPE,
)

#: Stable capability keys.  Ordered for display and artifact stability.
CAPABILITY_KEYS: tuple[str, ...] = (
    "transition:cut",
    "transition:dissolve",
    "transition:fade",
    "transition:wipe",
    "transition:push",
    "transition:slide",
    "track:multi_video",
    "gap:explicit",
    "source:mixed_rate",
    "clip:reverse",
    "media:baked",
)

_CAP_DESCRIPTIONS = {
    "transition:cut": "hard cut with an editable edit point",
    "transition:dissolve": "dissolve with two editable source ranges",
    "transition:fade": "fade with two editable source ranges",
    "transition:wipe": "wipe with two editable source ranges",
    "transition:push": "push with two editable source ranges",
    "transition:slide": "slide with two editable source ranges",
    "track:multi_video": "more than one simultaneous video track",
    "gap:explicit": "an explicit empty interval object on a track",
    "source:mixed_rate": "mixed source frame rates alongside the record rate",
    "clip:reverse": "a clip that plays its source range backwards",
    "media:baked": "reference a pre-baked (flattened) media asset",
}

#: Outcomes that count as an honest, faithful expression of a capability.
REVERSIBLE = "reversible"

#: The measured outcome a profile should produce for a capability it claims to
#: support.  ``gap:explicit`` is reported as a loss by CMX yet survives as a
#: reconstructed gap, so it is reversible in practice.
SUPPORTED_OUTCOME = REVERSIBLE


@dataclass(frozen=True)
class TargetProfile:
    """The exact capability set a target exchange format can express."""

    profile_id: str
    format: str
    label: str
    transitions: frozenset[str]
    multi_video_track: bool = False
    explicit_gaps: bool = False
    mixed_rate: bool = False
    reverse: bool = False
    baked_media: bool = True

    def supports_transition(self, transition: str) -> bool:
        return transition in self.transitions

    def supports(self, key: str) -> bool:
        if key.startswith("transition:"):
            return self.supports_transition(key.split(":", 1)[1])
        return {
            "track:multi_video": self.multi_video_track,
            "gap:explicit": self.explicit_gaps,
            "source:mixed_rate": self.mixed_rate,
            "clip:reverse": self.reverse,
            "media:baked": self.baked_media,
        }[key]

    def capability_rows(self) -> list[dict]:
        return [
            {
                "capability": key,
                "description": _CAP_DESCRIPTIONS[key],
                "supported": self.supports(key),
            }
            for key in CAPABILITY_KEYS
        ]

    def to_jsonable(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "format": self.format,
            "label": self.label,
            "capabilities": {
                row["capability"]: row["supported"] for row in self.capability_rows()
            },
        }


TL_JSON_PROFILE = TargetProfile(
    profile_id="tl-json",
    format="tl-json",
    label="TL-JSON v1 (rich, lossless)",
    transitions=frozenset(SUPPORTED_TRANSITIONS),
    multi_video_track=True,
    explicit_gaps=True,
    mixed_rate=True,
    reverse=True,
    baked_media=True,
)

#: CMX 3600 has one ``D`` code shared by dissolves.  A fade (to/from black) is
#: therefore *not* exactly expressible: it re-reads as a dissolve, which loses
#: the edit semantic even though the picture survives.  The profile records
#: that honestly instead of claiming fade support.
CMX_PROFILE = TargetProfile(
    profile_id="cmx3600",
    format="cmx3600",
    label="CMX 3600 EDL (single video track)",
    transitions=frozenset({TRANSITION_CUT, TRANSITION_DISSOLVE, TRANSITION_WIPE}),
    multi_video_track=False,
    explicit_gaps=False,
    # Per-source rates and their timecode origins survive the EDL round trip
    # because each source is parsed on its own clock; the warning is about
    # retiming *metadata*, not about the sampled frames.
    mixed_rate=True,
    reverse=False,
    # CMX can *address* baked media by reel name but cannot record that the
    # asset is flattened, so a later relink no longer knows to ask for the
    # baked asset rather than two ranges.  That identity loss is reported.
    baked_media=False,
)


def narrow(profile: TargetProfile, *drop: str, profile_id: str | None = None) -> TargetProfile:
    """Return a strict subset of ``profile`` with the given capabilities removed.

    ``drop`` accepts capability keys (``transition:wipe``) or bare transition
    names (``wipe``).
    """
    transitions = set(profile.transitions)
    flags = {
        "track:multi_video": profile.multi_video_track,
        "gap:explicit": profile.explicit_gaps,
        "source:mixed_rate": profile.mixed_rate,
        "clip:reverse": profile.reverse,
        "media:baked": profile.baked_media,
    }
    for key in drop:
        if key in SUPPORTED_TRANSITIONS:
            transitions.discard(key)
        elif key.startswith("transition:"):
            transitions.discard(key.split(":", 1)[1])
        elif key in flags:
            flags[key] = False
        else:
            raise KeyError(f"unknown capability {key!r}")
    # A cut is the irreducible fallback; keep it even if asked to drop it.
    transitions.add(TRANSITION_CUT)
    new_id = profile_id or (
        profile.profile_id
        + "-minus-" + "-".join(sorted(d.replace("transition:", "") for d in drop))
    )
    return replace(
        profile,
        profile_id=new_id,
        label=f"{profile.label} — narrowed ({', '.join(sorted(drop))})",
        transitions=frozenset(transitions),
        multi_video_track=flags["track:multi_video"],
        explicit_gaps=flags["gap:explicit"],
        mixed_rate=flags["source:mixed_rate"],
        reverse=flags["clip:reverse"],
        baked_media=flags["media:baked"],
    )


#: Selectable profiles.  The narrowed profile exists to prove that capability
#: changes propagate to the actual exchange and to the contract outcomes.
NARROW_WIPE_PROFILE = narrow(
    CMX_PROFILE, "transition:wipe", profile_id="cmx3600-narrow-wipe"
)

NARROW_GAP_PROFILE = narrow(
    CMX_PROFILE, "gap:explicit", profile_id="cmx3600-narrow-gap"
)

PROFILES: dict[str, TargetProfile] = {
    TL_JSON_PROFILE.profile_id: TL_JSON_PROFILE,
    CMX_PROFILE.profile_id: CMX_PROFILE,
    NARROW_WIPE_PROFILE.profile_id: NARROW_WIPE_PROFILE,
    NARROW_GAP_PROFILE.profile_id: NARROW_GAP_PROFILE,
}


def get_profile(profile_id: str | None, fmt: str | None = None) -> TargetProfile:
    """Resolve a profile id, or fall back to the full profile for ``fmt``."""
    if profile_id:
        if profile_id not in PROFILES:
            raise KeyError(f"unknown target profile {profile_id!r}")
        return PROFILES[profile_id]
    if fmt == "tl-json":
        return TL_JSON_PROFILE
    if fmt == "cmx3600":
        return CMX_PROFILE
    raise KeyError(f"no profile for format {fmt!r}")


GRADUAL_TRANSITIONS = frozenset(
    {TRANSITION_DISSOLVE, TRANSITION_FADE, TRANSITION_WIPE, "push", "slide"}
)


def fallback_transition(transition: str, profile: TargetProfile) -> str:
    """The semantic a profile substitutes when it cannot express ``transition``.

    A gradual transition that the target lacks degrades to a hard cut: the
    source frames are unchanged, but the cross-fade/move is gone and the edit
    point collapses to one instant.  This matches the documented CMX contract
    (``push``/``slide`` export as ``C``).
    """
    if profile.supports_transition(transition):
        return transition
    return TRANSITION_CUT


def profiles_jsonable() -> list[dict]:
    return [p.to_jsonable() for p in PROFILES.values()]


def manifest(profile: TargetProfile, contract_results: Iterable[dict] | None = None) -> dict:
    """Join the capability list with the executable contract samples.

    Every capability row points at the samples that exercise it; a capability
    with no sample is a documentation hole and is flagged as such.
    """
    results = list(contract_results or [])
    by_capability: dict[str, list[dict]] = {k: [] for k in CAPABILITY_KEYS}
    for row in results:
        for cap in row.get("capabilities", []):
            by_capability.setdefault(cap, []).append(
                {
                    "sample": row["sample"],
                    "outcome": row["outcome"],
                    "first_loss": row.get("first_loss"),
                }
            )
    capabilities = []
    for key in CAPABILITY_KEYS:
        samples = by_capability.get(key, [])
        capabilities.append(
            {
                "capability": key,
                "description": _CAP_DESCRIPTIONS[key],
                "supported": profile.supports(key),
                "samples": samples,
                "has_contract": bool(samples),
            }
        )
    return {
        "profile": profile.to_jsonable(),
        "capabilities": capabilities,
        "uncovered_capabilities": [
            c["capability"] for c in capabilities if not c["has_contract"]
        ],
    }
