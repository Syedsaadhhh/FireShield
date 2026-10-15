"""
FireShield Asynchronous Player Gate & Fire OS ExoPlayer Adapter Contract (P0 / P3).

TARGET PLATFORM SELECTION:
- Target OS: Amazon Fire OS (Android-based Fire TV devices)
- Target Player API: AndroidX Media3 ExoPlayer (androidx.media3.exoplayer.ExoPlayer)
  inside a publisher-owned Fire TV application playing publisher-authorized HLS/DASH streams.

ASYNCHRONOUS SEEK & SURFACE BLANKING INVARIANTS (P0):
- Media3 ExoPlayer.seekTo(positionMs) is asynchronous; the decoder and surface do NOT
  reach target_ms synchronously when seekTo() returns.
- FireShieldExoPlayerAdapter keeps the player PAUSED and the shutter surface BLANKED
  for the entire duration of an in-flight seek.
- The shutter is unblanked and playback resumes ONLY after an explicit
  on_seek_completed_callback (mapping to Player.Listener.onPositionDiscontinuity with
  DISCONTINUITY_REASON_SEEK) confirms:
    1. The callback's seek_token matches the currently active in-flight seek (not stale).
    2. The callback's event_id has not been replayed.
    3. The seek completed within seek_timeout_ms.
    4. The active rendition_set_id and rendition_playlist_sha256 have not changed mid-seek.
    5. The actual landing position_ms >= target_seek_ms (safe_resume_ms).
- On ANY uncertain state (stale callback, replayed event, seek rejection, timeout,
  backward/short keyframe landing, or rendition change mid-seek), the adapter
  immediately pauses and blanks the surface and transitions to FireShieldState.ERROR.
"""

from __future__ import annotations

import dataclasses
import shutil
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, runtime_checkable

from state_engine import (
    FireShieldRuntime,
    FireShieldState,
    PlaybackSessionContext,
    PlayerActionType,
    PlayerCommand,
)

DEFAULT_SEEK_TIMEOUT_MS = 3000


@dataclasses.dataclass(frozen=True)
class InFlightSeekOperation:
    seek_token: int
    target_seek_ms: int
    interval_id: Optional[str]
    expected_rendition_set_id: str
    expected_playlist_sha256: str
    initiated_at_monotonic_ms: int
    timeout_ms: int


@runtime_checkable
class FireOSExoPlayerPort(Protocol):
    """
    Minimal asynchronous contract mapping to AndroidX Media3 ExoPlayer APIs on Fire OS:
    - pause()                             -> exoPlayer.pause()
    - play()                              -> exoPlayer.play()
    - seek_to_ms_async(target_ms, token)  -> exoPlayer.seekTo(target_ms) (async)
    - set_shutter_surface_blanked(b)      -> playerView.shutterView.visibility = VISIBLE/INVISIBLE
    """

    def get_current_position_ms(self) -> int:
        ...

    def is_playing(self) -> bool:
        ...

    def pause(self) -> None:
        ...

    def play(self) -> None:
        ...

    def seek_to_ms_async(self, target_ms: int, seek_token: int) -> bool:
        ...

    def set_shutter_surface_blanked(self, blanked: bool) -> None:
        ...

    def show_intervention_banner(
        self,
        *,
        reason_code: str,
        interval_id: Optional[str],
        context_cue_ids: Tuple[str, ...],
        target_seek_ms: Optional[int],
    ) -> None:
        ...

    def hide_intervention_banner(self) -> None:
        ...


def check_fireos_sdk_and_device_status() -> Dict[str, Any]:
    """
    Probes the host machine for Android/Fire OS 'adb' and Vega CLI tools.
    Reports honest status without claiming device/simulator execution when absent.
    """
    adb_path = shutil.which("adb")
    if not adb_path:
        import pathlib
        default_adb = pathlib.Path(r"C:\Users\ATEC\AppData\Local\Android\Sdk\platform-tools\adb.exe")
        if default_adb.exists():
            adb_path = str(default_adb)
    vega_path = shutil.which("vega") or shutil.which("kepler")
    return {
        "target_platform": "Fire OS (AndroidX Media3 ExoPlayer)",
        "adb_installed": adb_path is not None,
        "adb_path": adb_path,
        "vega_sdk_installed": vega_path is not None,
        "fire_tv_device_or_simulator_attached": False,
        "fire_tv_playback_claimed": False,
        "status": "NATIVE_MODULE_PRESENT_NO_DEVICE_OR_EMULATOR_ATTACHED",
    }


FIRE_OS_EXOPLAYER_VERIFICATION_CHECKLIST: Tuple[Dict[str, str], ...] = (
    {
        "gate_id": "FTV-GATE-01",
        "title": "Pre-Event Pause Before First Hazard Frame on Owned HLS Stream",
        "target_api": "ExoPlayer.createMessage(PlayerMessage.Target).setPosition(startMs - preGuardMs)",
        "pass_criteria": (
            "On physical Fire TV Stick or Fire OS emulator via ADB, player pauses and shutter view "
            "blanks at >= 1 frame before interval.start_ms with zero hazardous frames rendered."
        ),
        "current_status": "PENDING_ATTACHED_FIRE_OS_DEVICE_OR_AVD",
    },
    {
        "gate_id": "FTV-GATE-02",
        "title": "Reject Stale Sidecar After Re-Encode, Intro Edit, or Ad Splice",
        "target_api": "Player.Listener.onTimelineChanged / HlsManifest playlist SHA-256 check",
        "pass_criteria": (
            "Loading a re-encoded or intro-shifted HLS playlist with a previous revision's sidecar "
            "immediately transitions adapter to ERROR, pauses ExoPlayer, and blanks the shutter view."
        ),
        "current_status": "PENDING_ATTACHED_FIRE_OS_DEVICE_OR_AVD",
    },
    {
        "gate_id": "FTV-GATE-03",
        "title": "Asynchronous Seek to safe_resume_ms with PositionDiscontinuity Confirmation",
        "target_api": "ExoPlayer.seekTo(safeResumeMs) + SeekParameters.NEXT_SYNC + onPositionDiscontinuity",
        "pass_criteria": (
            "Shutter stays blanked while seek is in flight; unblanks only after onPositionDiscontinuity "
            "confirms positionMs >= safe_resume_ms on the expected rendition."
        ),
        "current_status": "PENDING_ATTACHED_FIRE_OS_DEVICE_OR_AVD",
    },
)


class FireShieldExoPlayerAdapter:
    """
    Asynchronous fail-closed bridge between FireShieldRuntime and a publisher-owned
    FireOSExoPlayerPort. Never assumes seek_to_ms_async finishes synchronously.
    """

    def __init__(
        self,
        player: FireOSExoPlayerPort,
        runtime: FireShieldRuntime,
        *,
        seek_timeout_ms: int = DEFAULT_SEEK_TIMEOUT_MS,
    ):
        if seek_timeout_ms <= 0:
            raise ValueError("seek_timeout_ms must be positive.")
        self.player = player
        self.runtime = runtime
        self.seek_timeout_ms = seek_timeout_ms

        self.command_history: List[PlayerCommand] = []
        self.in_flight_seek: Optional[InFlightSeekOperation] = None
        self._next_seek_token: int = 0
        self._completed_or_invalidated_tokens: Set[int] = set()
        self._processed_event_ids: Set[str] = set()
        self._last_monotonic_ms: int = 0

        # Enforce initial contract state before any frame can play
        if self.runtime.state == FireShieldState.ERROR:
            self._force_pause_and_blank(
                reason_code=self.runtime.error_reason or "INITIAL_CONTRACT_ERROR"
            )
        elif self.runtime.state == FireShieldState.UNREVIEWED:
            self._force_pause_and_blank(reason_code="UNREVIEWED_ASSET_HOLD")

    def _force_pause_and_blank(
        self,
        *,
        reason_code: str,
        interval_id: Optional[str] = None,
        context_cue_ids: Tuple[str, ...] = (),
        target_seek_ms: Optional[int] = None,
    ) -> None:
        self.player.pause()
        self.player.set_shutter_surface_blanked(True)
        self.player.show_intervention_banner(
            reason_code=reason_code,
            interval_id=interval_id,
            context_cue_ids=context_cue_ids,
            target_seek_ms=target_seek_ms,
        )

    def _fail_closed_uncertain_state(
        self,
        *,
        position_ms: int,
        reason_code: str,
        detail: str,
    ) -> PlayerCommand:
        if self.in_flight_seek is not None:
            self._completed_or_invalidated_tokens.add(self.in_flight_seek.seek_token)
            self.in_flight_seek = None

        fail_cmd = self.runtime.notify_seek_failed(position_ms, reason=f"{reason_code}:{detail}")
        self._force_pause_and_blank(
            reason_code=reason_code,
            interval_id=fail_cmd.interval_id,
            context_cue_ids=fail_cmd.context_cue_ids,
            target_seek_ms=None,
        )
        self.command_history.append(fail_cmd)
        return fail_cmd

    def _initiate_async_seek(self, cmd: PlayerCommand, now_monotonic_ms: int) -> PlayerCommand:
        assert cmd.target_seek_ms is not None
        self._next_seek_token += 1
        token = self._next_seek_token

        # Always pause and blank surface BEFORE dispatching async seek
        self._force_pause_and_blank(
            reason_code=cmd.reason_code,
            interval_id=cmd.interval_id,
            context_cue_ids=cmd.context_cue_ids,
            target_seek_ms=cmd.target_seek_ms,
        )

        self.in_flight_seek = InFlightSeekOperation(
            seek_token=token,
            target_seek_ms=cmd.target_seek_ms,
            interval_id=cmd.interval_id,
            expected_rendition_set_id=self.runtime.session.rendition_set_id,
            expected_playlist_sha256=self.runtime.session.rendition_playlist_sha256,
            initiated_at_monotonic_ms=now_monotonic_ms,
            timeout_ms=self.seek_timeout_ms,
        )

        accepted = self.player.seek_to_ms_async(cmd.target_seek_ms, seek_token=token)
        if not accepted:
            return self._fail_closed_uncertain_state(
                position_ms=self.player.get_current_position_ms(),
                reason_code="ASYNC_SEEK_DISPATCH_REJECTED",
                detail=f"Player rejected seek_to_ms_async({cmd.target_seek_ms}) for token={token}",
            )
        return cmd

    def _apply_command(self, cmd: PlayerCommand, now_monotonic_ms: int) -> PlayerCommand:
        self.command_history.append(cmd)

        if cmd.action == PlayerActionType.ERROR:
            if self.in_flight_seek is not None:
                self._completed_or_invalidated_tokens.add(self.in_flight_seek.seek_token)
                self.in_flight_seek = None
            self._force_pause_and_blank(
                reason_code=cmd.reason_code,
                interval_id=cmd.interval_id,
                context_cue_ids=cmd.context_cue_ids,
            )
            return cmd

        if cmd.action == PlayerActionType.PAUSE:
            self._force_pause_and_blank(
                reason_code=cmd.reason_code,
                interval_id=cmd.interval_id,
                context_cue_ids=cmd.context_cue_ids,
                target_seek_ms=cmd.target_seek_ms,
            )
            return cmd

        if cmd.action == PlayerActionType.HOLD:
            self.player.pause()
            self.player.set_shutter_surface_blanked(True)
            return cmd

        if cmd.action == PlayerActionType.SEEK:
            if cmd.target_seek_ms is None:
                return self._fail_closed_uncertain_state(
                    position_ms=cmd.presentation_ms,
                    reason_code="SEEK_COMMAND_MISSING_TARGET",
                    detail="PlayerCommand.SEEK emitted without target_seek_ms",
                )
            return self._initiate_async_seek(cmd, now_monotonic_ms=now_monotonic_ms)

        if cmd.action == PlayerActionType.CONTINUE:
            # Never unblank or play if a seek is still in flight!
            if self.in_flight_seek is not None:
                self.player.pause()
                self.player.set_shutter_surface_blanked(True)
                return cmd
            self.player.set_shutter_surface_blanked(False)
            self.player.hide_intervention_banner()
            if not self.player.is_playing():
                self.player.play()
            return cmd

        return self._fail_closed_uncertain_state(
            position_ms=cmd.presentation_ms,
            reason_code="UNKNOWN_PLAYER_ACTION",
            detail=f"Unhandled action {cmd.action!r}",
        )

    def on_playback_tick(
        self,
        presentation_ms: Optional[int] = None,
        *,
        now_monotonic_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> PlayerCommand:
        """
        Handles periodic player progress ticks.
        If a seek is currently in flight, checks for seek timeout and keeps the
        surface paused and blanked until on_seek_completed_callback arrives.
        """
        if now_monotonic_ms is not None:
            self._last_monotonic_ms = now_monotonic_ms
        else:
            self._last_monotonic_ms += 33

        pos = self.player.get_current_position_ms() if presentation_ms is None else presentation_ms

        if event_id is not None:
            if event_id in self._processed_event_ids:
                return self._fail_closed_uncertain_state(
                    position_ms=pos,
                    reason_code="REPLAYED_TICK_EVENT_REJECTED",
                    detail=f"Duplicate event_id={event_id!r}",
                )
            self._processed_event_ids.add(event_id)

        if self.in_flight_seek is not None:
            elapsed = self._last_monotonic_ms - self.in_flight_seek.initiated_at_monotonic_ms
            if elapsed > self.in_flight_seek.timeout_ms:
                return self._fail_closed_uncertain_state(
                    position_ms=pos,
                    reason_code="IN_FLIGHT_SEEK_TIMED_OUT",
                    detail=f"Seek token={self.in_flight_seek.seek_token} exceeded {self.in_flight_seek.timeout_ms}ms",
                )
            # Keep video paused and shutter blanked while seek is still in flight
            self.player.pause()
            self.player.set_shutter_surface_blanked(True)
            hold_cmd = PlayerCommand(
                action=PlayerActionType.HOLD,
                state=FireShieldState.INTERVENING,
                presentation_ms=pos,
                target_seek_ms=self.in_flight_seek.target_seek_ms,
                reason_code="AWAITING_ASYNC_SEEK_COMPLETION_HOLD",
                requires_surface_blanking=True,
                interval_id=self.in_flight_seek.interval_id,
                audit_evidence={
                    "in_flight_seek_token": self.in_flight_seek.seek_token,
                    "elapsed_ms": elapsed,
                },
            )
            self.command_history.append(hold_cmd)
            return hold_cmd

        cmd = self.runtime.update_player_head(pos)
        return self._apply_command(cmd, now_monotonic_ms=self._last_monotonic_ms)

    def on_seek_completed_callback(
        self,
        *,
        seek_token: int,
        actual_position_ms: int,
        active_rendition_set_id: Optional[str] = None,
        active_playlist_sha256: Optional[str] = None,
        now_monotonic_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> PlayerCommand:
        """
        Explicit callback invoked when ExoPlayer reports onPositionDiscontinuity(DISCONTINUITY_REASON_SEEK).
        Unblanks and resumes ONLY if token, timing, rendition identity, and landing >= target_seek_ms match.
        """
        if now_monotonic_ms is not None:
            self._last_monotonic_ms = now_monotonic_ms

        if event_id is not None:
            if event_id in self._processed_event_ids:
                return self._fail_closed_uncertain_state(
                    position_ms=actual_position_ms,
                    reason_code="REPLAYED_SEEK_CALLBACK_REJECTED",
                    detail=f"Replayed callback event_id={event_id!r}",
                )
            self._processed_event_ids.add(event_id)

        if seek_token in self._completed_or_invalidated_tokens:
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="STALE_OR_REPLAYED_SEEK_TOKEN",
                detail=f"Seek token={seek_token} was already completed or invalidated",
            )

        if self.in_flight_seek is None:
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="UNEXPECTED_SEEK_CALLBACK_NO_SEEK_IN_FLIGHT",
                detail=f"Received seek_token={seek_token} with no active in-flight seek",
            )

        if seek_token != self.in_flight_seek.seek_token:
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="STALE_SEEK_TOKEN_MISMATCH",
                detail=f"Callback token={seek_token} != active token={self.in_flight_seek.seek_token}",
            )

        elapsed = self._last_monotonic_ms - self.in_flight_seek.initiated_at_monotonic_ms
        if elapsed < 0 or elapsed > self.in_flight_seek.timeout_ms:
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="SEEK_CALLBACK_TIMEOUT_EXCEEDED",
                detail=f"Callback arrived after {elapsed}ms (timeout={self.in_flight_seek.timeout_ms}ms)",
            )

        rend_set = (
            active_rendition_set_id
            if active_rendition_set_id is not None
            else self.runtime.session.rendition_set_id
        )
        playlist_sha = (
            active_playlist_sha256
            if active_playlist_sha256 is not None
            else self.runtime.session.rendition_playlist_sha256
        )
        if (
            rend_set != self.in_flight_seek.expected_rendition_set_id
            or playlist_sha != self.in_flight_seek.expected_playlist_sha256
        ):
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="RENDITION_MISMATCH_ON_SEEK_COMPLETION",
                detail=(
                    f"Expected ({self.in_flight_seek.expected_rendition_set_id}, "
                    f"{self.in_flight_seek.expected_playlist_sha256}), "
                    f"got ({rend_set}, {playlist_sha})"
                ),
            )

        target_ms = self.in_flight_seek.target_seek_ms
        if actual_position_ms < target_ms:
            return self._fail_closed_uncertain_state(
                position_ms=actual_position_ms,
                reason_code="SEEK_LANDED_BEFORE_SAFE_RESUME_MS",
                detail=f"actual_position_ms={actual_position_ms} < target_seek_ms={target_ms}",
            )

        # Mark token consumed and clear in_flight_seek before delegating to runtime
        self._completed_or_invalidated_tokens.add(seek_token)
        self.in_flight_seek = None

        resolved_cmd = self.runtime.update_player_head(actual_position_ms)
        return self._apply_command(resolved_cmd, now_monotonic_ms=self._last_monotonic_ms)

    def on_user_seek(self, target_ms: int, *, now_monotonic_ms: Optional[int] = None) -> PlayerCommand:
        if now_monotonic_ms is not None:
            self._last_monotonic_ms = now_monotonic_ms
        if self.in_flight_seek is not None:
            self._completed_or_invalidated_tokens.add(self.in_flight_seek.seek_token)
            self.in_flight_seek = None
        cmd = self.runtime.notify_seek(target_ms)
        return self._apply_command(cmd, now_monotonic_ms=self._last_monotonic_ms)

    def on_viewer_confirm_bypass_seek(self, *, now_monotonic_ms: Optional[int] = None) -> PlayerCommand:
        if now_monotonic_ms is not None:
            self._last_monotonic_ms = now_monotonic_ms
        cmd = self.runtime.request_interval_bypass_seek()
        return self._apply_command(cmd, now_monotonic_ms=self._last_monotonic_ms)

    def on_rendition_or_playlist_changed(
        self,
        new_session: PlaybackSessionContext,
        new_sidecar_str: Optional[str],
        *,
        now_monotonic_ms: Optional[int] = None,
    ) -> PlayerCommand:
        """
        If a rendition change occurs while a seek is in flight, invalidates the in-flight
        seek immediately and fails closed (or re-evaluates under paused+blanked surface).
        """
        if now_monotonic_ms is not None:
            self._last_monotonic_ms = now_monotonic_ms

        had_in_flight_seek = self.in_flight_seek is not None
        if self.in_flight_seek is not None:
            self._completed_or_invalidated_tokens.add(self.in_flight_seek.seek_token)
            self.in_flight_seek = None

        # Always pause and blank during rendition transition
        self.player.pause()
        self.player.set_shutter_surface_blanked(True)

        cmd = self.runtime.on_rendition_switch(new_session, new_sidecar_str)
        if had_in_flight_seek and cmd.action == PlayerActionType.CONTINUE:
            # Rendition changed mid-seek: never auto-unblank without confirming safe position
            return self._fail_closed_uncertain_state(
                position_ms=self.player.get_current_position_ms(),
                reason_code="RENDITION_SWITCH_DURING_IN_FLIGHT_SEEK",
                detail="Rendition switched while a hazard-bypass seek was still in flight",
            )
        return self._apply_command(cmd, now_monotonic_ms=self._last_monotonic_ms)


@dataclasses.dataclass
class AsyncInMemoryExoPlayerDouble:
    """
    Asynchronous in-memory player double for adversarial P0/P3 unit tests.
    Does NOT update position_ms when seek_to_ms_async() is called; position updates
    only when the test harness explicitly simulates decoder completion.
    Explicitly NOT a Fire TV hardware device or emulator.
    """
    position_ms: int = 0
    playing: bool = True
    shutter_blanked: bool = False
    banner_visible: bool = False
    last_banner_reason: Optional[str] = None
    last_banner_cues: Tuple[str, ...] = ()
    reject_all_seeks: bool = False
    pending_async_seeks: List[Tuple[int, int]] = dataclasses.field(default_factory=list)
    events: List[str] = dataclasses.field(default_factory=list)

    def get_current_position_ms(self) -> int:
        return self.position_ms

    def is_playing(self) -> bool:
        return self.playing

    def pause(self) -> None:
        self.playing = False
        self.events.append(f"pause@{self.position_ms}")

    def play(self) -> None:
        self.playing = True
        self.events.append(f"play@{self.position_ms}")

    def seek_to_ms_async(self, target_ms: int, seek_token: int) -> bool:
        if self.reject_all_seeks:
            self.events.append(f"async_seek_rejected@{self.position_ms}->target={target_ms}(token={seek_token})")
            return False
        self.pending_async_seeks.append((seek_token, target_ms))
        self.events.append(f"async_seek_queued@{self.position_ms}->target={target_ms}(token={seek_token})")
        return True

    def complete_latest_seek(self, landing_offset_ms: int = 0) -> Tuple[int, int]:
        """Simulates the hardware decoder landing at target_ms + landing_offset_ms."""
        if not self.pending_async_seeks:
            raise RuntimeError("No pending async seeks to complete.")
        token, target_ms = self.pending_async_seeks.pop(-1)
        self.position_ms = max(0, target_ms + landing_offset_ms)
        self.events.append(f"async_seek_landed@{self.position_ms}(token={token},target={target_ms})")
        return token, self.position_ms

    def set_shutter_surface_blanked(self, blanked: bool) -> None:
        self.shutter_blanked = blanked
        self.events.append(f"shutter_blank={blanked}@{self.position_ms}")

    def show_intervention_banner(
        self,
        *,
        reason_code: str,
        interval_id: Optional[str],
        context_cue_ids: Tuple[str, ...],
        target_seek_ms: Optional[int],
    ) -> None:
        self.banner_visible = True
        self.last_banner_reason = reason_code
        self.last_banner_cues = context_cue_ids
        self.events.append(f"banner_show:{reason_code}@{self.position_ms}")

    def hide_intervention_banner(self) -> None:
        self.banner_visible = False
        self.events.append(f"banner_hide@{self.position_ms}")


# Alias for backward compatibility in imports
InMemoryExoPlayerContractDouble = AsyncInMemoryExoPlayerDouble
