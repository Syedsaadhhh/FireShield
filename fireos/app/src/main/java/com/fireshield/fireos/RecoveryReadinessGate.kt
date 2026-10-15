package com.fireshield.fireos

enum class RecoveryReadinessStatus { WAITING, READY_TO_VERIFY, TIMED_OUT, STALE }

/** Pure recovery state: no player action is permitted until all evidence belongs to this operation. */
class RecoveryReadinessGate(private val settleMs: Long = 150L) {
    private var activeToken: Long? = null
    private var deadlineMs = 0L
    private var lastChangeMs = 0L
    private var seekConfirmed = false
    private var decoderReady = false
    private var tracksKnown = false
    private var frameRendered = false

    fun begin(token: Long, nowMs: Long, timeoutMs: Long, seekIsNoOp: Boolean = false) {
        require(timeoutMs > settleMs && token > 0L)
        activeToken = token
        deadlineMs = nowMs + timeoutMs
        lastChangeMs = nowMs
        seekConfirmed = seekIsNoOp
        decoderReady = false
        tracksKnown = false
        frameRendered = false
    }

    private fun update(token: Long, nowMs: Long, change: () -> Unit): Boolean {
        if (token != activeToken || nowMs > deadlineMs) return false
        change()
        lastChangeMs = nowMs
        return true
    }

    fun onSeekConfirmed(token: Long, nowMs: Long) = update(token, nowMs) { seekConfirmed = true }
    fun onDecoderReady(token: Long, ready: Boolean, nowMs: Long) = update(token, nowMs) { decoderReady = ready }
    fun onTracksKnown(token: Long, known: Boolean, nowMs: Long) = update(token, nowMs) { tracksKnown = known }
    fun onFrameRendered(token: Long, nowMs: Long) = update(token, nowMs) { frameRendered = true }

    fun status(token: Long, nowMs: Long): RecoveryReadinessStatus {
        if (token != activeToken) return RecoveryReadinessStatus.STALE
        // A timeout is a failure even if the position/track fields happen to match.
        if (nowMs > deadlineMs) return RecoveryReadinessStatus.TIMED_OUT
        return if (seekConfirmed && decoderReady && tracksKnown && frameRendered &&
            nowMs - lastChangeMs >= settleMs) RecoveryReadinessStatus.READY_TO_VERIFY
        else RecoveryReadinessStatus.WAITING
    }

    fun clear() { activeToken = null }
}
