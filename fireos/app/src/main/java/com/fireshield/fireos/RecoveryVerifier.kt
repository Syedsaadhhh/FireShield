package com.fireshield.fireos

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import kotlin.math.abs

data class ObservedPlayerState(
    val positionMs: Long,
    val captionsActive: Boolean,
    val activeSubtitleTrackId: String?,
    val activeAudioTrackId: String?,
    val activePresentationMode: String,
    val activeRenditionPlaylistSha256: String,
    val surfaceBlanked: Boolean,
    val isPlaying: Boolean
)

data class RecoveryVerificationResult(
    val status: String, // "VERIFIED_MATCH" or "DIVERGENCE_DETECTED"
    val mismatchFields: List<String>,
    val positionDeltaMs: Long,
    val expectedValues: Map<String, Any?>,
    val observedValues: Map<String, Any?>,
    val diagnosticCode: String
)

object RecoveryVerifier {
    const val DEFAULT_POSITION_TOLERANCE_MS = 1500L
    private val HOSTILE_CHAR_REGEX = Regex("[\u0000-\u001f\u007f<>{};'\"`]")

    fun verify(
        checkpoint: ContinuityCheckpoint,
        observed: ObservedPlayerState,
        positionToleranceMs: Long = DEFAULT_POSITION_TOLERANCE_MS
    ): RecoveryVerificationResult {
        val mismatches = mutableListOf<String>()

        val expected = mapOf<String, Any?>(
            "position_ms" to checkpoint.positionMs,
            "captions_enabled" to checkpoint.captionsEnabled,
            "selected_subtitle_track_id" to checkpoint.selectedSubtitleTrackId,
            "selected_audio_track_id" to checkpoint.selectedAudioTrackId,
            "presentation_mode" to checkpoint.presentationMode,
            "rendition_playlist_sha256" to checkpoint.renditionPlaylistSha256
        )

        val actual = mapOf<String, Any?>(
            "position_ms" to observed.positionMs,
            "captions_enabled" to observed.captionsActive,
            "selected_subtitle_track_id" to observed.activeSubtitleTrackId,
            "selected_audio_track_id" to observed.activeAudioTrackId,
            "presentation_mode" to observed.activePresentationMode,
            "rendition_playlist_sha256" to observed.activeRenditionPlaylistSha256
        )

        // 1. Edition / Playlist digest check (detects cut shifts, new intros, re-encodes)
        if (checkpoint.renditionPlaylistSha256 != observed.activeRenditionPlaylistSha256) {
            mismatches.add("rendition_playlist_sha256")
        }

        // 2. Position tolerance check
        val posDelta = abs(observed.positionMs - checkpoint.positionMs)
        if (posDelta > positionToleranceMs) {
            mismatches.add("position_ms")
        }

        // 3. Caption / Subtitle check
        if (checkpoint.captionsEnabled != observed.captionsActive) {
            mismatches.add("captions_enabled")
        } else if (checkpoint.captionsEnabled && checkpoint.selectedSubtitleTrackId != null) {
            if (observed.activeSubtitleTrackId != checkpoint.selectedSubtitleTrackId) {
                mismatches.add("selected_subtitle_track_id")
            }
        }

        // 4. Presentation mode check
        if (checkpoint.presentationMode != observed.activePresentationMode) {
            mismatches.add("presentation_mode")
        }

        // 5. Audio track check
        if (checkpoint.selectedAudioTrackId != null) {
            if (observed.activeAudioTrackId == null) {
                mismatches.add("selected_audio_track_id")
            } else if (checkpoint.selectedAudioTrackId != observed.activeAudioTrackId) {
                mismatches.add("selected_audio_track_id")
            }
        }

        val status = if (mismatches.isEmpty()) "VERIFIED_MATCH" else "DIVERGENCE_DETECTED"
        val diagCode = if (mismatches.isEmpty()) "RESTORE_VERIFIED_PASS" else "ERR_MISMATCH_" + mismatches.joinToString("_").uppercase()

        return RecoveryVerificationResult(
            status = status,
            mismatchFields = mismatches,
            positionDeltaMs = posDelta,
            expectedValues = expected,
            observedValues = actual,
            diagnosticCode = diagCode
        )
    }

    fun deriveRetestFixtureId(mismatchFields: List<String>): String {
        return when {
            mismatchFields.contains("captions_enabled") || mismatchFields.contains("selected_subtitle_track_id") ->
                "DROPPED_CAPTION_FAULT_V1"
            mismatchFields.contains("rendition_playlist_sha256") ->
                "STALE_CUT_INTRO_DRIFT_V1"
            mismatchFields.contains("position_ms") ->
                "POSITION_OVERSHOOT_FAULT_V1"
            mismatchFields.contains("selected_audio_track_id") ->
                "DROPPED_AUDIO_FAULT_V1"
            mismatchFields.contains("presentation_mode") ->
                "PRESENTATION_MODE_FAULT_V1"
            mismatchFields.isEmpty() ->
                "BENIGN_SUBTITLE_CONTINUITY_V1"
            else ->
                "UNKNOWN_RECOVERY_FAULT_V1"
        }
    }

    fun computeReceiptDigest(obj: JSONObject): String {
        val copy = JSONObject()
        val keys = obj.keys().asSequence().toList().sorted()
        for (k in keys) {
            if (k != "receipt_digest") {
                copy.put(k, obj.opt(k))
            }
        }
        val canonical = FireShieldContractValidator.canonicalizeJsonValue(copy)
        val sha = java.security.MessageDigest.getInstance("SHA-256")
            .digest(canonical.toByteArray(java.nio.charset.StandardCharsets.UTF_8))
        return "sha256:" + sha.joinToString("") { "%02x".format(it) }
    }

    fun validateReceiptDigest(receipt: JSONObject): Boolean {
        val declared = receipt.optString("receipt_digest", "")
        if (declared.isBlank()) return false
        val computed = computeReceiptDigest(receipt)
        return declared == computed
    }

    fun sanitizeReceiptString(input: String?, maxLen: Int = 128): String {
        if (input == null) return ""
        val trimmed = input.trim()
        val bounded = if (trimmed.length > maxLen) trimmed.substring(0, maxLen) else trimmed
        if (HOSTILE_CHAR_REGEX.containsMatchIn(bounded)) {
            throw FireShieldContractException("Hostile or unsafe characters detected in receipt value: $bounded")
        }
        return bounded
    }

    /**
     * Generates a privacy-safe, strictly allowlisted JSON incident receipt with SHA-256 integrity digest.
     * Contains zero video buffers, zero viewing credentials, zero auth tokens.
     */
    fun generateIncidentReceipt(
        incidentId: String = UUID.randomUUID().toString(),
        fixtureId: String,
        verificationResult: RecoveryVerificationResult,
        appVersion: String = "0.2.0",
        platformName: String = "FireOS-Android-Leanback",
        retestFixtureId: String? = null
    ): JSONObject {
        val cleanIncidentId = sanitizeReceiptString(incidentId)
        val cleanFixtureId = sanitizeReceiptString(fixtureId)
        val cleanAppVersion = sanitizeReceiptString(appVersion)
        val cleanPlatform = sanitizeReceiptString(platformName)
        val chosenRetestId = retestFixtureId ?: deriveRetestFixtureId(verificationResult.mismatchFields)
        val cleanRetestFixtureId = sanitizeReceiptString(chosenRetestId)

        val isoDf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }

        val receipt = JSONObject().apply {
            put("receipt_schema_version", 1)
            put("incident_id", cleanIncidentId)
            put("fixture_id", cleanFixtureId)
            put("timestamp_iso", isoDf.format(Date()))

            put("environment", JSONObject().apply {
                put("app_version", cleanAppVersion)
                put("platform", cleanPlatform)
            })

            put("recovery_status", verificationResult.status)
            put("diagnostic_code", verificationResult.diagnosticCode)

            val mismatchesArray = JSONArray()
            verificationResult.mismatchFields.forEach { mismatchesArray.put(it) }
            put("mismatch_fields", mismatchesArray)
            put("position_delta_ms", verificationResult.positionDeltaMs)

            val expObj = JSONObject()
            verificationResult.expectedValues.forEach { (k, v) -> expObj.put(k, v ?: JSONObject.NULL) }
            put("expected_state", expObj)

            val obsObj = JSONObject()
            verificationResult.observedValues.forEach { (k, v) -> obsObj.put(k, v ?: JSONObject.NULL) }
            put("observed_state", obsObj)

            put("privacy_attestation", JSONObject().apply {
                put("contains_pii", false)
                put("contains_video_buffers", false)
                put("contains_auth_tokens", false)
            })

            put("retest_fixture_id", cleanRetestFixtureId)
        }

        receipt.put("receipt_digest", computeReceiptDigest(receipt))
        return receipt
    }
}
