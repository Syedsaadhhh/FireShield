package com.fireshield.fireos

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.TrackGroup
import androidx.media3.common.Tracks
import org.junit.Assert.*
import org.junit.Test

class SelectedTrackIdentityTest {
    private fun tracks(selected: BooleanArray): Tracks {
        val group = TrackGroup("captions",
            Format.Builder().setId("caption-en").setLanguage("en").setSampleMimeType(MimeTypes.TEXT_VTT).build(),
            Format.Builder().setId("caption-en-alt").setLanguage("en").setSampleMimeType(MimeTypes.TEXT_VTT).build())
        return Tracks(listOf(Tracks.Group(group, false, intArrayOf(C.FORMAT_HANDLED, C.FORMAT_HANDLED), selected)))
    }

    @Test fun selectedSecondTrackIsNotReportedAsFirstTrack() {
        assertEquals("caption-en-alt", SelectedTrackIdentity.selectedId(tracks(booleanArrayOf(false, true)), C.TRACK_TYPE_TEXT))
    }
    @Test fun missingRequiredTrackDoesNotInventAnIdentity() {
        assertNull(SelectedTrackIdentity.selectedId(tracks(booleanArrayOf(false, false)), C.TRACK_TYPE_TEXT))
        assertNull(SelectedTrackIdentity.selectedId(tracks(booleanArrayOf(true, false)), C.TRACK_TYPE_AUDIO))
    }
    @Test fun presentationObservationCanDivergeFromExpectedPolicy() {
        val cp = ContinuityCheckpoint.create(assetRevisionId = "asset",
            renditionSetId = "rendition", renditionPlaylistSha256 = "sha256:" + "0".repeat(64),
            positionMs = 1000L, captionsEnabled = true, selectedSubtitleTrackId = "caption-en",
            presentationMode = "reduced_effects")
        val result = RecoveryVerifier.verify(cp, ObservedPlayerState(positionMs = 1000L,
            captionsActive = true, activeSubtitleTrackId = "caption-en", activeAudioTrackId = null,
            activePresentationMode = "standard", activeRenditionPlaylistSha256 = cp.renditionPlaylistSha256,
            surfaceBlanked = true, isPlaying = false))
        assertEquals("DIVERGENCE_DETECTED", result.status)
        assertEquals(listOf("presentation_mode"), result.mismatchFields)
        assertEquals("PRESENTATION_MODE_FAULT_V1", RecoveryVerifier.deriveRetestFixtureId(result.mismatchFields))
    }
}
