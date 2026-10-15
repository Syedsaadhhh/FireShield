package com.fireshield.fireos

import org.junit.Assert.*
import org.junit.Test

class RecoveryReadinessGateTest {
    @Test fun requiresSeekReadinessTracksAndRenderedFrame() {
        val g = RecoveryReadinessGate()
        g.begin(1L, 1000L, 3000L)
        g.onDecoderReady(1L, true, 1050L)
        g.onTracksKnown(1L, true, 1060L)
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(1L, 1300L))
        g.onSeekConfirmed(1L, 1300L)
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(1L, 1500L))
        g.onFrameRendered(1L, 1500L)
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(1L, 1550L))
        assertEquals(RecoveryReadinessStatus.READY_TO_VERIFY, g.status(1L, 1700L))
    }
    @Test fun timeoutNeverTurnsMatchingFieldsIntoSuccess() {
        val g = RecoveryReadinessGate()
        g.begin(1L, 0L, 1000L, seekIsNoOp = true)
        g.onTracksKnown(1L, true, 100L)
        assertEquals(RecoveryReadinessStatus.TIMED_OUT, g.status(1L, 1001L))
        assertFalse(g.onDecoderReady(1L, true, 1002L))
        assertFalse(g.onFrameRendered(1L, 1002L))
        assertEquals(RecoveryReadinessStatus.TIMED_OUT, g.status(1L, 1100L))
    }
    @Test fun oldOperationCannotReleaseNewRecovery() {
        val g = RecoveryReadinessGate()
        g.begin(1L, 0L, 2000L)
        g.begin(2L, 200L, 2000L)
        assertFalse(g.onSeekConfirmed(1L, 250L))
        assertFalse(g.onFrameRendered(1L, 250L))
        assertEquals(RecoveryReadinessStatus.STALE, g.status(1L, 500L))
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(2L, 500L))
    }
    @Test fun bufferingAfterReadinessReturnsToWaiting() {
        val g = RecoveryReadinessGate()
        g.begin(1L, 0L, 2000L, seekIsNoOp = true)
        g.onTracksKnown(1L, true, 10L)
        g.onDecoderReady(1L, true, 20L)
        g.onFrameRendered(1L, 30L)
        assertEquals(RecoveryReadinessStatus.READY_TO_VERIFY, g.status(1L, 200L))
        g.onDecoderReady(1L, false, 210L)
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(1L, 500L))
    }
    @Test fun explicitNoOpSeekStillRequiresRealPlayerEvidence() {
        val g = RecoveryReadinessGate()
        g.begin(3L, 0L, 2000L, seekIsNoOp = true)
        assertEquals(RecoveryReadinessStatus.WAITING, g.status(3L, 400L))
        g.onDecoderReady(3L, true, 410L)
        g.onTracksKnown(3L, true, 420L)
        g.onFrameRendered(3L, 430L)
        assertEquals(RecoveryReadinessStatus.READY_TO_VERIFY, g.status(3L, 600L))
        g.clear()
        assertEquals(RecoveryReadinessStatus.STALE, g.status(3L, 610L))
    }
}
