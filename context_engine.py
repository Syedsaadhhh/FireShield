"""
Synaptic Story Context Engine — Subtitle Grounding, Editorial Review, and Bedrock Adapter.

DESIGN & SAFETY INVARIANTS:
1. Subtitle & Cue Grounding: Context notes must be strictly grounded in publisher-authorized
   subtitle/transcript cues around the hazard interval.
2. Editorial Approval Gate: Context notes require explicit human editorial review before
   publication and cannot be published or displayed in unapproved state.
3. Cryptographic Binding: Approved context notes are bound to the asset revision and interval ID,
   included in the canonical sidecar manifest dictionary, and covered by the Ed25519 v2 signature.
4. Bedrock Advisory & Safety: Bedrock Converse adapter is strictly opt-in and read-only.
   It requires valid AWS credentials, a configured model/inference profile ID, and explicit
   spend authorization (FIRESHIELD_BEDROCK_SPEND_AUTHORIZED=true).
   When credentials or spend authorization are missing, it returns deterministic extractive
   fallback with model_invoked=false.
5. No Playback Control: Language models never determine hazard bounds, signatures, seek targets,
   or playback authorization.
"""

from __future__ import annotations

import dataclasses
import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("fireshield.context")

# Strict regex for cue IDs: alphanumeric, underscores, hyphens, periods, colons
CUE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.\:]+$")
MAX_CUE_TEXT_LENGTH = 1000
MAX_NOTE_TEXT_LENGTH = 1000
SUPPORTED_NOTE_GENERATION_METHODS = frozenset({
    "extractive_subtitles",
    "editorial_manual",
    "bedrock_converse",
})


class ContextError(Exception):
    """Raised when cue validation, grounding, or context note invariants fail."""
    pass


@dataclasses.dataclass(frozen=True)
class SubtitleCue:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str
    language: str = "en"

    def __post_init__(self):
        if not isinstance(self.cue_id, str) or not CUE_ID_REGEX.match(self.cue_id.strip()):
            raise ContextError(f"Invalid cue_id: {self.cue_id!r} (must match {CUE_ID_REGEX.pattern})")
        if not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool) or self.start_ms < 0:
            raise ContextError(f"Cue start_ms must be a non-negative integer: {self.start_ms!r}")
        if not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool) or self.end_ms < self.start_ms:
            raise ContextError(f"Cue end_ms ({self.end_ms!r}) must be >= start_ms ({self.start_ms!r})")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ContextError("Cue text must be a non-empty string")
        if len(self.text) > MAX_CUE_TEXT_LENGTH:
            raise ContextError(f"Cue text exceeds maximum length of {MAX_CUE_TEXT_LENGTH} characters")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": html.escape(self.text.strip()),
            "language": self.language,
        }


@dataclasses.dataclass
class GroundedContextNote:
    note_id: str
    interval_id: str
    asset_revision_id: str
    text: str
    source_cue_ids: List[str]
    generation_method: str = "extractive_subtitles"
    editorial_approved: bool = False
    reviewer_id: Optional[str] = None
    language: str = "en"
    model_invoked: bool = False
    model_id: Optional[str] = None
    notes: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.note_id, str) or not self.note_id.strip():
            raise ContextError("note_id must be a non-empty string")
        if not isinstance(self.interval_id, str) or not self.interval_id.strip():
            raise ContextError("interval_id must be a non-empty string")
        if not isinstance(self.asset_revision_id, str) or not self.asset_revision_id.startswith("sha256:"):
            raise ContextError("asset_revision_id must be a sha256-prefixed string")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ContextError("Context note text must be a non-empty string")
        if len(self.text) > MAX_NOTE_TEXT_LENGTH:
            raise ContextError(f"Context note text exceeds maximum length of {MAX_NOTE_TEXT_LENGTH} characters")
        if self.generation_method not in SUPPORTED_NOTE_GENERATION_METHODS:
            raise ContextError(f"Unsupported generation_method: {self.generation_method!r}")
        if not isinstance(self.source_cue_ids, list) or len(self.source_cue_ids) == 0:
            raise ContextError("source_cue_ids must be a non-empty list of cue IDs")

    def to_sidecar_dict(self) -> Dict[str, Any]:
        """Compact dictionary format suitable for sidecar inclusion."""
        return {
            "note_id": self.note_id,
            "interval_id": self.interval_id,
            "text": html.escape(self.text.strip()),
            "source_cue_ids": list(self.source_cue_ids),
            "generation_method": self.generation_method,
            "editorial_approved": self.editorial_approved,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full representation for API and editorial review."""
        return {
            "note_id": self.note_id,
            "interval_id": self.interval_id,
            "asset_revision_id": self.asset_revision_id,
            "text": self.text,
            "source_cue_ids": list(self.source_cue_ids),
            "generation_method": self.generation_method,
            "editorial_approved": self.editorial_approved,
            "reviewer_id": self.reviewer_id,
            "language": self.language,
            "model_invoked": self.model_invoked,
            "model_id": self.model_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GroundedContextNote:
        return cls(
            note_id=str(data["note_id"]),
            interval_id=str(data["interval_id"]),
            asset_revision_id=str(data["asset_revision_id"]),
            text=str(data["text"]),
            source_cue_ids=list(data.get("source_cue_ids", [])),
            generation_method=str(data.get("generation_method", "extractive_subtitles")),
            editorial_approved=bool(data.get("editorial_approved", False)),
            reviewer_id=data.get("reviewer_id"),
            language=str(data.get("language", "en")),
            model_invoked=bool(data.get("model_invoked", False)),
            model_id=data.get("model_id"),
            notes=data.get("notes"),
        )


def parse_webvtt_cues(vtt_content: str, language: str = "en") -> List[SubtitleCue]:
    """
    Parses standard WebVTT subtitle text into validated SubtitleCue instances.
    Extracts timestamps (HH:MM:SS.mmm or MM:SS.mmm) and sequential/explicit cue IDs.
    """
    if not vtt_content or not vtt_content.strip():
        return []

    lines = [line.strip() for line in vtt_content.splitlines()]
    cues: List[SubtitleCue] = []
    
    # Simple WebVTT timestamp parser: (00:)00:00.000
    timestamp_re = re.compile(
        r"^(?:(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3}))\s+-->\s+(?:(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3}))"
    )

    def parse_time_to_ms(hours: Optional[str], mins: str, secs: str, ms: str) -> int:
        h = int(hours) if hours else 0
        m = int(mins)
        s = int(secs)
        milli = int(ms)
        return (h * 3600 + m * 60 + s) * 1000 + milli

    i = 0
    cue_counter = 1
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
            i += 1
            continue

        cue_id = f"cue-{cue_counter:03d}"
        # Check if this line is an explicit cue ID followed by timestamp line
        if i + 1 < len(lines) and timestamp_re.match(lines[i + 1]):
            clean_id = re.sub(r"[^a-zA-Z0-9_\-\.\:]", "", line)
            if clean_id:
                cue_id = clean_id
            i += 1
            line = lines[i]

        match = timestamp_re.match(line)
        if match:
            start_ms = parse_time_to_ms(match.group(1), match.group(2), match.group(3), match.group(4))
            end_ms = parse_time_to_ms(match.group(5), match.group(6), match.group(7), match.group(8))
            
            # Read subsequent lines as text until empty line
            i += 1
            text_lines = []
            while i < len(lines) and lines[i]:
                # Strip HTML-like tags from WebVTT (e.g. <v Speaker>, <b>, </i>)
                cleaned = re.sub(r"<[^>]+>", "", lines[i]).strip()
                if cleaned:
                    text_lines.append(cleaned)
                i += 1

            full_text = " ".join(text_lines).strip()
            if full_text:
                cues.append(
                    SubtitleCue(
                        cue_id=cue_id,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=full_text,
                        language=language,
                    )
                )
                cue_counter += 1
        else:
            i += 1

    return cues


def extract_grounded_context_for_interval(
    *,
    interval_id: str,
    interval_start_ms: int,
    interval_end_ms: int,
    asset_revision_id: str,
    cues: Sequence[SubtitleCue],
    window_before_ms: int = 4000,
    window_after_ms: int = 4000,
    language: str = "en",
) -> Optional[GroundedContextNote]:
    """
    Finds subtitle cues that overlap or fall within the specified window around a hazard interval,
    and constructs an extractive dialogue context note.
    """
    earliest_ms = max(0, interval_start_ms - window_before_ms)
    latest_ms = interval_end_ms + window_after_ms

    matching_cues = [
        c for c in cues
        if not (c.end_ms < earliest_ms or c.start_ms > latest_ms)
    ]

    if not matching_cues:
        return None

    # Sort matching cues chronologically
    matching_cues.sort(key=lambda c: c.start_ms)
    source_cue_ids = [c.cue_id for c in matching_cues]
    
    # Construct extractive context sentence
    extracted_text = " ".join(c.text for c in matching_cues)
    note_id = f"note-{interval_id}"

    return GroundedContextNote(
        note_id=note_id,
        interval_id=interval_id,
        asset_revision_id=asset_revision_id,
        text=extracted_text,
        source_cue_ids=source_cue_ids,
        generation_method="extractive_subtitles",
        editorial_approved=False,
        language=language,
        model_invoked=False,
        model_id=None,
    )


class AmazonBedrockConverseContextAdapter:
    """
    Adapter for Amazon Bedrock Converse API for narrative context note generation.
    Strictly opt-in, bounded spend, and read-only.
    Fails closed to local extractive fallback whenever credentials, spend authorization,
    or model IDs are unconfigured.
    """

    def __init__(
        self,
        *,
        model_id: Optional[str] = None,
        region_name: Optional[str] = None,
    ):
        self.model_id = model_id or os.environ.get("FIRESHIELD_BEDROCK_MODEL_ID")
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._spend_authorized = os.environ.get("FIRESHIELD_BEDROCK_SPEND_AUTHORIZED", "").lower() in ("true", "1")

    def is_cloud_ready(self) -> bool:
        """Checks whether Bedrock Converse can be invoked with verified credentials and authorized spend."""
        if not self._spend_authorized:
            return False
        if not self.model_id or not self.model_id.strip():
            return False
        # Check AWS credential environment
        has_env_keys = bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        has_profile = bool(os.environ.get("AWS_PROFILE"))
        return has_env_keys or has_profile

    def get_readiness_status(self) -> Dict[str, Any]:
        ready = self.is_cloud_ready()
        return {
            "bedrock_converse_available": ready,
            "configured_model_id": self.model_id if self.model_id else None,
            "region": self.region_name,
            "spend_authorized": self._spend_authorized,
            "status": "CONFIGURED_ACTIVE" if ready else "UNCONFIGURED_OFFLINE",
        }

    def generate_context_note(
        self,
        *,
        interval_id: str,
        interval_start_ms: int,
        interval_end_ms: int,
        asset_revision_id: str,
        cues: Sequence[SubtitleCue],
        language: str = "en",
    ) -> GroundedContextNote:
        """
        Attempts to generate an editorial narrative summary using Bedrock Converse API.
        If credentials/spend are unconfigured, automatically falls back to deterministic
        extractive subtitle context with model_invoked=false.
        """
        # 1. First get base extractive grounding
        base_note = extract_grounded_context_for_interval(
            interval_id=interval_id,
            interval_start_ms=interval_start_ms,
            interval_end_ms=interval_end_ms,
            asset_revision_id=asset_revision_id,
            cues=cues,
            language=language,
        )

        if not base_note:
            raise ContextError(f"No subtitle cues found around interval '{interval_id}' to ground context")

        # 2. Check if live cloud invocation is permitted
        if not self.is_cloud_ready():
            logger.info("Bedrock Converse offline/unauthorized; returning extractive subtitle context")
            return base_note

        # 3. Live Bedrock invocation with bounded token limit
        try:
            import boto3
            from botocore.config import Config

            boto_config = Config(
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=5,
                read_timeout=10,
            )
            client = boto3.client("bedrock-runtime", region_name=self.region_name, config=boto_config)

            prompt = (
                f"You are providing an accessibility narrative note for viewers during an edited "
                f"or blanked video interval. The dialogue during this interval is:\n"
                f"\"{base_note.text}\"\n\n"
                f"Summarize the key story action or narrative point in 1-2 concise, neutral sentences "
                f"(max 250 characters). Do not invent characters or plot elements not present in the dialogue."
            )

            messages = [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]

            response = client.converse(
                modelId=self.model_id,
                messages=messages,
                inferenceConfig={
                    "maxTokens": 100,
                    "temperature": 0.0,
                },
            )

            output_text = (
                response.get("output", {})
                .get("message", {})
                .get("content", [{}])[0]
                .get("text", "")
                .strip()
            )

            if output_text:
                return GroundedContextNote(
                    note_id=base_note.note_id,
                    interval_id=interval_id,
                    asset_revision_id=asset_revision_id,
                    text=output_text,
                    source_cue_ids=base_note.source_cue_ids,
                    generation_method="bedrock_converse",
                    editorial_approved=False,
                    language=language,
                    model_invoked=True,
                    model_id=self.model_id,
                )

        except Exception as exc:
            logger.warning("Bedrock Converse invocation failed (%s); falling back to extractive note", exc)

        # Safe fallback
        return base_note
