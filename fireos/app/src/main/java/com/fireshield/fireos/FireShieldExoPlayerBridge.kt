package com.fireshield.fireos

import android.view.View
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlin.math.max

data class InFlightSeekState(
    val seekToken: Long,
    val targetSeekMs: Long,
    val intervalId: String?,
    val expectedRenditionSetId: String,
    val expectedPlaylistSha256: String,
    val initiatedAtMonotonicMs: Long,
    val timeoutMs: Long
)

interface NativeExoPlayerPort {
    fun getCurrentPositionMs(): Long
    fun isPlaying(): Boolean
    fun pause()
    fun play()
    fun seekToMsAsync(targetMs: Long, seekToken: Long): Boolean
    fun setShutterSurfaceBlanked(blanked: Boolean)
    fun showInterventionBanner(
        reasonCode: String,
        intervalId: String?,
        contextCueIds: List<String>,
        targetSeekMs: Long?
    )
    fun showInterventionBanner(
        reasonCode: String,
        intervalId: String?,
        contextCueIds: List<String>,
        targetSeekMs: Long?,
        contextNote: String?
    ) {
        showInterventionBanner(reasonCode, intervalId, contextCueIds, targetSeekMs)
    }
    fun hideInterventionBanner()
}

/**
 * Concrete AndroidX Media3 ExoPlayer port implementation for Fire OS.
 */
class Media3ExoPlayerController(
    private val exoPlayer: ExoPlayer,
    private val playerView: PlayerView,
    private val explicitShutterOverlay: View,
    private val onBannerUpdate: (Boolean, String) -> Unit
) : NativeExoPlayerPort {

    private var lastSubmittedSeekToken: Long = 0L

    fun getLastSubmittedSeekToken(): Long = lastSubmittedSeekToken

    override fun getCurrentPositionMs(): Long = max(0L, exoPlayer.currentPosition)

    override fun isPlaying(): Boolean = exoPlayer.isPlaying

    override fun pause() {
        exoPlayer.pause()
    }

    override fun play() {
        exoPlayer.play()
    }

    override fun seekToMsAsync(targetMs: Long, seekToken: Long): Boolean {
        if (targetMs < 0L) return false
        lastSubmittedSeekToken = seekToken
        exoPlayer.seekTo(targetMs)
        return true
    }

    override fun setShutterSurfaceBlanked(blanked: Boolean) {
        val vis = if (blanked) View.VISIBLE else View.GONE
        explicitShutterOverlay.visibility = vis
        playerView.findViewById<View>(androidx.media3.ui.R.id.exo_shutter)?.visibility = vis
    }

    override fun showInterventionBanner(
        reasonCode: String,
        intervalId: String?,
        contextCueIds: List<String>,
        targetSeekMs: Long?
    ) {
        showInterventionBanner(reasonCode, intervalId, contextCueIds, targetSeekMs, null)
    }

    override fun showInterventionBanner(
        reasonCode: String,
        intervalId: String?,
        contextCueIds: List<String>,
        targetSeekMs: Long?,
        contextNote: String?
    ) {
        val cues = if (contextCueIds.isEmpty()) "none" else contextCueIds.joinToString(",")
        val noteSuffix = if (!contextNote.isNullOrBlank()) " | Note: \"$contextNote\"" else ""
        val msg = "INTERVENTION [$reasonCode] interval=${intervalId ?: "N/A"} targetSeek=${targetSeekMs ?: "N/A"} cues=$cues$noteSuffix"
        onBannerUpdate(true, msg)
    }

    override fun hideInterventionBanner() {
        onBannerUpdate(false, "PLAYBACK_SAFE_CONTINUE")
    }
}

data class FireShieldAuditEvent(
    val seq: Long,
    val tenantId: String,
    val assetRevisionId: String,
    val renditionSetId: String,
    val keyId: String?,
    val schemaVersion: Int?,
    val state: FireShieldState,
    val action: PlayerActionType,
    val reasonCode: String,
    val presentationMs: Long,
    val monotonicMs: Long,
    val intervalId: String? = null
)

fun interface FireShieldAuditSink {
    fun onAuditEvent(event: FireShieldAuditEvent)
}

/**
 * Asynchronous, fail-closed Fire OS player gate bridge consuming v2 Ed25519 sidecar
 * contracts and enforcing pre-event pause, shutter blanking, key rotation/revocation,
 * tenant-scoped audit emission, and async seek acknowledgement.
 */
class FireShieldPlayerGateBridge(
    private val player: NativeExoPlayerPort,
    session: PlaybackSessionDescriptor,
    sidecarJsonStr: String?,
    trustedPublicKeysHex: Map<String, String>,
    revokedKeyIds: Set<String> = emptySet(),
    private val preGuardMs: Long = 500L,
    private val seekTimeoutMs: Long = 3000L,
    private val auditSink: FireShieldAuditSink? = null
) {
    var session: PlaybackSessionDescriptor = session
        private set
    private var trustedKeys: Map<String, String> = trustedPublicKeysHex.toMap()
    private var revokedKeys: Set<String> = revokedKeyIds.toSet()
    private var rawSidecarJsonStr: String? = sidecarJsonStr

    var state: FireShieldState = FireShieldState.UNREVIEWED
        private set
    var verified: Boolean = false
        private set
    var errorReason: String? = null
        private set
    var intervals: List<HazardInterval> = emptyList()
        private set
    var activeKeyId: String? = null
        private set
    var activeSchemaVersion: Int? = null
        private set

    var inFlightSeek: InFlightSeekState? = null
        private set

    private var currentPositionMs: Long? = null
    private var activeInterval: HazardInterval? = null
    private var lastIntervenedIntervalId: String? = null
    private var nextSeekToken: Long = 0L
    private val consumedOrInvalidatedTokens = mutableSetOf<Long>()
    private val processedEventIds = mutableSetOf<String>()
    private var lastMonotonicMs: Long = 0L
    private var auditSeq: Long = 0L
    private val _auditHistory = mutableListOf<FireShieldAuditEvent>()

    val auditHistory: List<FireShieldAuditEvent>
        get() = _auditHistory.toList()

    init {
        loadAndVerifyContract(sidecarJsonStr)
    }

    private fun recordAudit(action: PlayerActionType, reasonCode: String, presentationMs: Long, intervalId: String? = null) {
        auditSeq += 1L
        val evt = FireShieldAuditEvent(
            seq = auditSeq,
            tenantId = session.tenantId,
            assetRevisionId = session.assetRevisionId,
            renditionSetId = session.renditionSetId,
            keyId = activeKeyId,
            schemaVersion = activeSchemaVersion,
            state = state,
            action = action,
            reasonCode = reasonCode,
            presentationMs = presentationMs,
            monotonicMs = lastMonotonicMs,
            intervalId = intervalId
        )
        _auditHistory.add(evt)
        auditSink?.onAuditEvent(evt)
    }

    private fun loadAndVerifyContract(sidecarJsonStr: String?) {
        rawSidecarJsonStr = sidecarJsonStr
        activeInterval = null
        if (inFlightSeek != null) {
            consumedOrInvalidatedTokens.add(inFlightSeek!!.seekToken)
            inFlightSeek = null
        }

        if (sidecarJsonStr == null) {
            state = FireShieldState.UNREVIEWED
            verified = false
            activeKeyId = null
            activeSchemaVersion = null
            errorReason = "NO_SIDECAR_PROVIDED"
            forcePauseAndBlank("UNREVIEWED_NO_SIDECAR", null, emptyList(), null)
            recordAudit(PlayerActionType.PAUSE, "UNREVIEWED_NO_SIDECAR", currentPositionMs ?: 0L)
            return
        }

        try {
            val contract = FireShieldContractValidator.validate(
                sidecarJsonStr = sidecarJsonStr,
                session = session,
                trustedPublicKeysHex = trustedKeys,
                revokedKeyIds = revokedKeys
            )
            intervals = contract.intervals
            activeKeyId = contract.keyId
            activeSchemaVersion = contract.schemaVersion
            when (contract.reviewState) {
                "rejected" -> {
                    state = FireShieldState.ERROR
                    verified = false
                    errorReason = "ASSET_EDITORIALLY_REJECTED"
                    forcePauseAndBlank("ASSET_EDITORIALLY_REJECTED", null, emptyList(), null)
                    recordAudit(PlayerActionType.ERROR, "ASSET_EDITORIALLY_REJECTED", currentPositionMs ?: 0L)
                }
                "approved" -> {
                    state = FireShieldState.ARMED
                    verified = true
                    errorReason = null
                    recordAudit(PlayerActionType.CONTINUE, "CONTRACT_VERIFIED_ARMED", currentPositionMs ?: 0L)
                }
                else -> {
                    state = FireShieldState.UNREVIEWED
                    verified = false
                    errorReason = "ASSET_NOT_APPROVED:${contract.reviewState}"
                    forcePauseAndBlank("UNREVIEWED_ASSET_HOLD", null, emptyList(), null)
                    recordAudit(PlayerActionType.PAUSE, "UNREVIEWED_ASSET_HOLD", currentPositionMs ?: 0L)
                }
            }
        } catch (e: Exception) {
            state = FireShieldState.ERROR
            verified = false
            activeKeyId = null
            activeSchemaVersion = null
            errorReason = e.message ?: "CONTRACT_VALIDATION_FAILED"
            forcePauseAndBlank(errorReason!!, null, emptyList(), null)
            recordAudit(PlayerActionType.ERROR, "CONTRACT_VALIDATION_FAILED:${errorReason}", currentPositionMs ?: 0L)
        }
    }

    fun updateKeyRing(
        newTrustedPublicKeysHex: Map<String, String>,
        newRevokedKeyIds: Set<String> = emptySet(),
        nowMonotonicMs: Long = lastMonotonicMs + 10L
    ): PlayerCommand {
        lastMonotonicMs = nowMonotonicMs
        trustedKeys = newTrustedPublicKeysHex.toMap()
        revokedKeys = newRevokedKeyIds.toSet()
        val pos = currentPositionMs ?: player.getCurrentPositionMs()
        loadAndVerifyContract(rawSidecarJsonStr)
        if (state == FireShieldState.ERROR || state == FireShieldState.UNREVIEWED) {
            return onPlaybackTick(pos, nowMonotonicMs)
        }
        currentPositionMs = null
        return evaluateTimelinePosition(
            presentationMs = pos,
            isExplicitSeek = false,
            isRenditionSwitch = true,
            seekCompletedIntervalId = null
        )
    }

    private fun forcePauseAndBlank(
        reasonCode: String,
        intervalId: String?,
        cues: List<String>,
        targetSeekMs: Long?,
        contextNote: String? = null
    ) {
        player.pause()
        player.setShutterSurfaceBlanked(true)
        player.showInterventionBanner(reasonCode, intervalId, cues, targetSeekMs, contextNote)
    }

    private fun failClosedUncertainState(positionMs: Long, reasonCode: String, detail: String): PlayerCommand {
        if (inFlightSeek != null) {
            consumedOrInvalidatedTokens.add(inFlightSeek!!.seekToken)
            inFlightSeek = null
        }
        state = FireShieldState.ERROR
        verified = false
        errorReason = "$reasonCode:$detail"
        val intervalId = activeInterval?.intervalId
        val cues = activeInterval?.contextCueIds ?: emptyList()
        forcePauseAndBlank(reasonCode, intervalId, cues, null)
        return PlayerCommand(
            action = PlayerActionType.ERROR,
            state = FireShieldState.ERROR,
            presentationMs = positionMs,
            reasonCode = reasonCode,
            requiresSurfaceBlanking = true,
            intervalId = intervalId,
            contextCueIds = cues
        )
    }

    private fun guardStartMs(interval: HazardInterval): Long = max(0L, interval.startMs - preGuardMs)

    private fun findMatchingInterval(
        prevMs: Long?,
        currentMs: Long,
        isExplicitSeek: Boolean,
        isRenditionSwitch: Boolean,
        seekCompletedIntervalId: String?
    ): Pair<HazardInterval, String>? {
        for (interval in intervals) {
            val gStart = guardStartMs(interval)
            if (currentMs in gStart..interval.endMs) {
                val kind = if (currentMs < interval.startMs) "PRE_GUARD_WINDOW" else "INSIDE_HAZARD_WINDOW"
                return Pair(interval, kind)
            }
            if (!isExplicitSeek && !isRenditionSwitch && prevMs != null &&
                prevMs < interval.startMs && currentMs > interval.endMs
            ) {
                if (seekCompletedIntervalId == interval.intervalId && currentMs >= interval.safeResumeMs) {
                    continue
                }
                if (lastIntervenedIntervalId != interval.intervalId || currentMs < interval.safeResumeMs) {
                    return Pair(interval, "PLAYHEAD_JUMPED_OVER_INTERVAL")
                }
            }
        }
        return null
    }

    private fun dispatchAsyncSeek(interval: HazardInterval, presentationMs: Long, reasonCode: String): PlayerCommand {
        nextSeekToken += 1L
        val token = nextSeekToken
        forcePauseAndBlank(reasonCode, interval.intervalId, interval.contextCueIds, interval.safeResumeMs)

        inFlightSeek = InFlightSeekState(
            seekToken = token,
            targetSeekMs = interval.safeResumeMs,
            intervalId = interval.intervalId,
            expectedRenditionSetId = session.renditionSetId,
            expectedPlaylistSha256 = session.renditionPlaylistSha256,
            initiatedAtMonotonicMs = lastMonotonicMs,
            timeoutMs = seekTimeoutMs
        )

        val accepted = player.seekToMsAsync(interval.safeResumeMs, token)
        if (!accepted) {
            return failClosedUncertainState(
                positionMs = presentationMs,
                reasonCode = "ASYNC_SEEK_DISPATCH_REJECTED",
                detail = "Player rejected seekToMsAsync(${interval.safeResumeMs})"
            )
        }
        return PlayerCommand(
            action = PlayerActionType.SEEK,
            state = FireShieldState.INTERVENING,
            presentationMs = presentationMs,
            reasonCode = reasonCode,
            requiresSurfaceBlanking = true,
            targetSeekMs = interval.safeResumeMs,
            intervalId = interval.intervalId,
            hazardTypes = interval.types,
            contextCueIds = interval.contextCueIds
        )
    }

    fun onPlaybackTick(
        presentationMs: Long = player.getCurrentPositionMs(),
        nowMonotonicMs: Long = lastMonotonicMs + 33L,
        eventId: String? = null
    ): PlayerCommand {
        lastMonotonicMs = nowMonotonicMs
        if (eventId != null) {
            if (!processedEventIds.add(eventId)) {
                return failClosedUncertainState(
                    positionMs = presentationMs,
                    reasonCode = "REPLAYED_TICK_EVENT_REJECTED",
                    detail = "Duplicate eventId=$eventId"
                )
            }
        }

        if (presentationMs < 0L) {
            return failClosedUncertainState(0L, "INVALID_PRESENTATION_TIMESTAMP", "Negative timestamp")
        }

        if (state == FireShieldState.ERROR) {
            forcePauseAndBlank(errorReason ?: "FAIL_CLOSED_ERROR", activeInterval?.intervalId, emptyList(), null)
            currentPositionMs = presentationMs
            return PlayerCommand(
                action = PlayerActionType.ERROR,
                state = FireShieldState.ERROR,
                presentationMs = presentationMs,
                reasonCode = "FAIL_CLOSED_CONTRACT_OR_RUNTIME_ERROR",
                requiresSurfaceBlanking = true
            )
        }

        if (!verified || state == FireShieldState.UNREVIEWED) {
            state = FireShieldState.UNREVIEWED
            forcePauseAndBlank("UNREVIEWED_ASSET_HOLD", null, emptyList(), null)
            currentPositionMs = presentationMs
            return PlayerCommand(
                action = PlayerActionType.PAUSE,
                state = FireShieldState.UNREVIEWED,
                presentationMs = presentationMs,
                reasonCode = "UNREVIEWED_ASSET_HOLD",
                requiresSurfaceBlanking = true
            )
        }

        // If an async seek is in flight, check timeout and hold paused+blanked
        val seekState = inFlightSeek
        if (seekState != null) {
            val elapsed = lastMonotonicMs - seekState.initiatedAtMonotonicMs
            if (elapsed > seekState.timeoutMs) {
                return failClosedUncertainState(
                    positionMs = presentationMs,
                    reasonCode = "IN_FLIGHT_SEEK_TIMED_OUT",
                    detail = "Elapsed ${elapsed}ms > timeout ${seekState.timeoutMs}ms"
                )
            }
            player.pause()
            player.setShutterSurfaceBlanked(true)
            return PlayerCommand(
                action = PlayerActionType.HOLD,
                state = FireShieldState.INTERVENING,
                presentationMs = presentationMs,
                reasonCode = "AWAITING_ASYNC_SEEK_COMPLETION_HOLD",
                requiresSurfaceBlanking = true,
                targetSeekMs = seekState.targetSeekMs,
                intervalId = seekState.intervalId
            )
        }

        return evaluateTimelinePosition(
            presentationMs = presentationMs,
            isExplicitSeek = false,
            isRenditionSwitch = false,
            seekCompletedIntervalId = null
        )
    }

    private fun evaluateTimelinePosition(
        presentationMs: Long,
        isExplicitSeek: Boolean,
        isRenditionSwitch: Boolean,
        seekCompletedIntervalId: String?
    ): PlayerCommand {
        val prevMs = currentPositionMs
        val match = findMatchingInterval(
            prevMs = prevMs,
            currentMs = presentationMs,
            isExplicitSeek = isExplicitSeek,
            isRenditionSwitch = isRenditionSwitch,
            seekCompletedIntervalId = seekCompletedIntervalId
        )

        if (match != null) {
            val (interval, triggerKind) = match
            if (state == FireShieldState.INTERVENING &&
                activeInterval?.intervalId == interval.intervalId &&
                prevMs == presentationMs &&
                !isExplicitSeek &&
                !isRenditionSwitch
            ) {
                player.pause()
                player.setShutterSurfaceBlanked(true)
                return PlayerCommand(
                    action = PlayerActionType.HOLD,
                    state = FireShieldState.INTERVENING,
                    presentationMs = presentationMs,
                    reasonCode = "DUPLICATE_TICK_INTERVENTION_HOLD",
                    requiresSurfaceBlanking = true,
                    intervalId = interval.intervalId,
                    hazardTypes = interval.types,
                    contextCueIds = interval.contextCueIds,
                    contextNote = interval.contextNote
                )
            }

            state = FireShieldState.INTERVENING
            activeInterval = interval
            lastIntervenedIntervalId = interval.intervalId
            currentPositionMs = presentationMs

            return when {
                interval.policy == "hold_playback" -> {
                    forcePauseAndBlank("POLICY_HOLD_$triggerKind", interval.intervalId, interval.contextCueIds, null, interval.contextNote)
                    PlayerCommand(
                        action = PlayerActionType.HOLD,
                        state = FireShieldState.INTERVENING,
                        presentationMs = presentationMs,
                        reasonCode = "POLICY_HOLD_$triggerKind",
                        requiresSurfaceBlanking = true,
                        intervalId = interval.intervalId,
                        hazardTypes = interval.types,
                        contextCueIds = interval.contextCueIds,
                        contextNote = interval.contextNote
                    )
                }
                interval.policy == "seek_to_safe_resume" ||
                    (interval.policy == "pause_or_skip" && session.allowAutoSeekOnPauseOrSkip) -> {
                    dispatchAsyncSeek(interval, presentationMs, "POLICY_SEEK_TO_SAFE_RESUME_$triggerKind")
                }
                else -> {
                    val reason = "POLICY_PREEMPTIVE_PAUSE_$triggerKind"
                    forcePauseAndBlank(reason, interval.intervalId, interval.contextCueIds, interval.safeResumeMs, interval.contextNote)
                    PlayerCommand(
                        action = PlayerActionType.PAUSE,
                        state = FireShieldState.INTERVENING,
                        presentationMs = presentationMs,
                        reasonCode = reason,
                        requiresSurfaceBlanking = true,
                        targetSeekMs = interval.safeResumeMs,
                        intervalId = interval.intervalId,
                        hazardTypes = interval.types,
                        contextCueIds = interval.contextCueIds,
                        contextNote = interval.contextNote
                    )
                }
            }
        }

        // Outside hazard intervals
        val active = activeInterval
        if (state == FireShieldState.INTERVENING && active != null) {
            if (presentationMs >= active.safeResumeMs) {
                val resolvedId = active.intervalId
                state = FireShieldState.RESOLVED
                activeInterval = null
                currentPositionMs = presentationMs
                player.setShutterSurfaceBlanked(false)
                player.hideInterventionBanner()
                if (!player.isPlaying()) {
                    player.play()
                }
                return PlayerCommand(
                    action = PlayerActionType.CONTINUE,
                    state = FireShieldState.RESOLVED,
                    presentationMs = presentationMs,
                    reasonCode = "HAZARD_INTERVAL_RESOLVED_AT_SAFE_RESUME",
                    requiresSurfaceBlanking = false,
                    intervalId = resolvedId
                )
            } else if (isExplicitSeek && presentationMs < guardStartMs(active)) {
                state = FireShieldState.ARMED
                activeInterval = null
                lastIntervenedIntervalId = null
                currentPositionMs = presentationMs
                player.setShutterSurfaceBlanked(false)
                player.hideInterventionBanner()
                if (!player.isPlaying()) {
                    player.play()
                }
                return PlayerCommand(
                    action = PlayerActionType.CONTINUE,
                    state = FireShieldState.ARMED,
                    presentationMs = presentationMs,
                    reasonCode = "REARMED_BEFORE_HAZARD_AFTER_SEEK",
                    requiresSurfaceBlanking = false
                )
            } else {
                currentPositionMs = presentationMs
                forcePauseAndBlank(
                    "HOLD_INSIDE_POST_HAZARD_SAFETY_MARGIN",
                    active.intervalId,
                    active.contextCueIds,
                    active.safeResumeMs
                )
                return PlayerCommand(
                    action = PlayerActionType.PAUSE,
                    state = FireShieldState.INTERVENING,
                    presentationMs = presentationMs,
                    reasonCode = "HOLD_INSIDE_POST_HAZARD_SAFETY_MARGIN",
                    requiresSurfaceBlanking = true,
                    targetSeekMs = active.safeResumeMs,
                    intervalId = active.intervalId
                )
            }
        }

        if (state == FireShieldState.RESOLVED && prevMs != null && presentationMs < prevMs) {
            lastIntervenedIntervalId = null
            state = FireShieldState.ARMED
        }

        currentPositionMs = presentationMs
        player.setShutterSurfaceBlanked(false)
        player.hideInterventionBanner()
        if (!player.isPlaying()) {
            player.play()
        }
        return PlayerCommand(
            action = PlayerActionType.CONTINUE,
            state = state,
            presentationMs = presentationMs,
            reasonCode = "PLAYBACK_SAFE_CONTINUE",
            requiresSurfaceBlanking = false
        )
    }

    fun requestViewerBypassSeek(nowMonotonicMs: Long = lastMonotonicMs + 10L): PlayerCommand {
        lastMonotonicMs = nowMonotonicMs
        val pos = currentPositionMs ?: player.getCurrentPositionMs()
        val active = activeInterval
        if (state != FireShieldState.INTERVENING || active == null) {
            player.pause()
            return PlayerCommand(
                action = PlayerActionType.HOLD,
                state = state,
                presentationMs = pos,
                reasonCode = "NO_ACTIVE_INTERVENTION_TO_BYPASS",
                requiresSurfaceBlanking = (state == FireShieldState.ERROR)
            )
        }
        return dispatchAsyncSeek(active, pos, "VIEWER_REQUESTED_SAFE_BYPASS_SEEK")
    }

    /**
     * Invoked when Media3 ExoPlayer fires Player.Listener.onPositionDiscontinuity
     * with Player.DISCONTINUITY_REASON_SEEK.
     */
    fun onPositionDiscontinuitySeekCompleted(
        seekToken: Long,
        actualPositionMs: Long,
        activeRenditionSetId: String = session.renditionSetId,
        activePlaylistSha256: String = session.renditionPlaylistSha256,
        nowMonotonicMs: Long = lastMonotonicMs + 50L,
        eventId: String? = null
    ): PlayerCommand {
        lastMonotonicMs = nowMonotonicMs

        if (eventId != null) {
            if (!processedEventIds.add(eventId)) {
                return failClosedUncertainState(
                    positionMs = actualPositionMs,
                    reasonCode = "REPLAYED_SEEK_CALLBACK_REJECTED",
                    detail = "Duplicate callback eventId=$eventId"
                )
            }
        }

        if (seekToken in consumedOrInvalidatedTokens) {
            return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "STALE_OR_REPLAYED_SEEK_TOKEN",
                detail = "Token $seekToken already consumed or invalidated"
            )
        }

        val currentSeek = inFlightSeek
            ?: return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "UNEXPECTED_SEEK_CALLBACK_NO_SEEK_IN_FLIGHT",
                detail = "No active seek in flight for token=$seekToken"
            )

        if (seekToken != currentSeek.seekToken) {
            return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "STALE_SEEK_TOKEN_MISMATCH",
                detail = "Token $seekToken != active ${currentSeek.seekToken}"
            )
        }

        val elapsed = lastMonotonicMs - currentSeek.initiatedAtMonotonicMs
        if (elapsed < 0L || elapsed > currentSeek.timeoutMs) {
            return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "SEEK_CALLBACK_TIMEOUT_EXCEEDED",
                detail = "Elapsed ${elapsed}ms > timeout ${currentSeek.timeoutMs}ms"
            )
        }

        if (activeRenditionSetId != currentSeek.expectedRenditionSetId ||
            activePlaylistSha256 != currentSeek.expectedPlaylistSha256
        ) {
            return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "RENDITION_MISMATCH_ON_SEEK_COMPLETION",
                detail = "Rendition changed during seek"
            )
        }

        if (actualPositionMs < currentSeek.targetSeekMs) {
            return failClosedUncertainState(
                positionMs = actualPositionMs,
                reasonCode = "SEEK_LANDED_BEFORE_SAFE_RESUME_MS",
                detail = "Landed at $actualPositionMs < target ${currentSeek.targetSeekMs}"
            )
        }

        consumedOrInvalidatedTokens.add(seekToken)
        inFlightSeek = null

        return evaluateTimelinePosition(
            presentationMs = actualPositionMs,
            isExplicitSeek = false,
            isRenditionSwitch = false,
            seekCompletedIntervalId = currentSeek.intervalId
        )
    }

    fun onRenditionOrPlaylistChanged(
        newSession: PlaybackSessionDescriptor,
        newSidecarJsonStr: String?,
        nowMonotonicMs: Long = lastMonotonicMs + 10L
    ): PlayerCommand {
        lastMonotonicMs = nowMonotonicMs
        val hadInFlightSeek = inFlightSeek != null
        val pos = currentPositionMs ?: player.getCurrentPositionMs()
        session = newSession
        player.pause()
        player.setShutterSurfaceBlanked(true)

        loadAndVerifyContract(newSidecarJsonStr)
        if (state == FireShieldState.ERROR || state == FireShieldState.UNREVIEWED) {
            return onPlaybackTick(pos, nowMonotonicMs)
        }
        if (hadInFlightSeek) {
            return failClosedUncertainState(
                positionMs = pos,
                reasonCode = "RENDITION_SWITCH_DURING_IN_FLIGHT_SEEK",
                detail = "Rendition switched while seek was in flight"
            )
        }
        currentPositionMs = null
        return evaluateTimelinePosition(
            presentationMs = pos,
            isExplicitSeek = false,
            isRenditionSwitch = true,
            seekCompletedIntervalId = null
        )
    }
}
