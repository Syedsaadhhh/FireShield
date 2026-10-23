package com.fireshield.fireos

import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.UUID

/**
 * Versioned local viewer continuity checkpoint.
 * Persists viewer's chosen playback state across app interruptions, crashes, or evictions.
 *
 * Security & Platform Boundaries:
 * - Completely separate from publisher-signed release metadata.
 * - Client NEVER holds or uses publisher private signing keys.
 * - Integrity is protected by a local SHA-256 checksum to detect accidental storage corruption.
 * - Atomic persistence via temp-file write, fsync, and atomic rename.
 */
data class ContinuityCheckpoint(
    val schemaVersion: Int = 1,
    val checkpointId: String = UUID.randomUUID().toString(),
    val timestampEpochMs: Long = System.currentTimeMillis(),
    val assetRevisionId: String,
    val renditionSetId: String,
    val renditionPlaylistSha256: String,
    val positionMs: Long,
    val captionsEnabled: Boolean,
    val selectedSubtitleTrackId: String? = null,
    val selectedAudioTrackId: String? = null,
    val presentationMode: String = "standard",
    val integrityDigest: String = ""
) {
    fun toJsonObject(): JSONObject {
        val obj = JSONObject()
        obj.put("schema_version", schemaVersion)
        obj.put("checkpoint_id", checkpointId)
        obj.put("timestamp_epoch_ms", timestampEpochMs)
        obj.put("asset_revision_id", assetRevisionId)
        obj.put("rendition_set_id", renditionSetId)
        obj.put("rendition_playlist_sha256", renditionPlaylistSha256)
        obj.put("position_ms", positionMs)
        obj.put("captions_enabled", captionsEnabled)
        obj.put("selected_subtitle_track_id", selectedSubtitleTrackId ?: JSONObject.NULL)
        obj.put("selected_audio_track_id", selectedAudioTrackId ?: JSONObject.NULL)
        obj.put("presentation_mode", presentationMode)
        obj.put("integrity_digest", integrityDigest)
        return obj
    }

    companion object {
        const val CURRENT_SCHEMA_VERSION = 1

        fun computeDigest(obj: JSONObject): String {
            val copy = JSONObject()
            val keys = obj.keys().asSequence().toList().sorted()
            for (k in keys) {
                if (k != "integrity_digest") {
                    copy.put(k, obj.opt(k))
                }
            }
            val canonical = FireShieldContractValidator.canonicalizeJsonValue(copy)
            val sha = MessageDigest.getInstance("SHA-256")
                .digest(canonical.toByteArray(StandardCharsets.UTF_8))
            return "sha256:" + sha.joinToString("") { "%02x".format(it) }
        }

        fun create(
            assetRevisionId: String,
            renditionSetId: String,
            renditionPlaylistSha256: String,
            positionMs: Long,
            captionsEnabled: Boolean,
            selectedSubtitleTrackId: String? = null,
            selectedAudioTrackId: String? = null,
            presentationMode: String = "standard",
            checkpointId: String = UUID.randomUUID().toString(),
            timestampEpochMs: Long = System.currentTimeMillis()
        ): ContinuityCheckpoint {
            val draft = JSONObject().apply {
                put("schema_version", CURRENT_SCHEMA_VERSION)
                put("checkpoint_id", checkpointId)
                put("timestamp_epoch_ms", timestampEpochMs)
                put("asset_revision_id", assetRevisionId)
                put("rendition_set_id", renditionSetId)
                put("rendition_playlist_sha256", renditionPlaylistSha256)
                put("position_ms", positionMs)
                put("captions_enabled", captionsEnabled)
                put("selected_subtitle_track_id", selectedSubtitleTrackId ?: JSONObject.NULL)
                put("selected_audio_track_id", selectedAudioTrackId ?: JSONObject.NULL)
                put("presentation_mode", presentationMode)
            }
            val digest = computeDigest(draft)
            return ContinuityCheckpoint(
                schemaVersion = CURRENT_SCHEMA_VERSION,
                checkpointId = checkpointId,
                timestampEpochMs = timestampEpochMs,
                assetRevisionId = assetRevisionId,
                renditionSetId = renditionSetId,
                renditionPlaylistSha256 = renditionPlaylistSha256,
                positionMs = positionMs,
                captionsEnabled = captionsEnabled,
                selectedSubtitleTrackId = selectedSubtitleTrackId,
                selectedAudioTrackId = selectedAudioTrackId,
                presentationMode = presentationMode,
                integrityDigest = digest
            )
        }

        fun fromJsonObject(obj: JSONObject): ContinuityCheckpoint {
            val schemaVersion = obj.optInt("schema_version", -1)
            if (schemaVersion != CURRENT_SCHEMA_VERSION) {
                throw FireShieldContractException("Unsupported checkpoint schema_version: $schemaVersion")
            }
            val declaredDigest = obj.optString("integrity_digest", "")
            if (declaredDigest.isBlank()) {
                throw FireShieldContractException("Checkpoint missing integrity_digest")
            }
            val computedDigest = computeDigest(obj)
            if (declaredDigest != computedDigest) {
                throw FireShieldContractException(
                    "Checkpoint integrity digest mismatch: declared=$declaredDigest, computed=$computedDigest"
                )
            }
            return ContinuityCheckpoint(
                schemaVersion = schemaVersion,
                checkpointId = obj.getString("checkpoint_id"),
                timestampEpochMs = obj.getLong("timestamp_epoch_ms"),
                assetRevisionId = obj.getString("asset_revision_id"),
                renditionSetId = obj.getString("rendition_set_id"),
                renditionPlaylistSha256 = obj.getString("rendition_playlist_sha256"),
                positionMs = obj.getLong("position_ms"),
                captionsEnabled = obj.getBoolean("captions_enabled"),
                selectedSubtitleTrackId = if (obj.isNull("selected_subtitle_track_id")) null else obj.getString("selected_subtitle_track_id"),
                selectedAudioTrackId = if (obj.isNull("selected_audio_track_id")) null else obj.getString("selected_audio_track_id"),
                presentationMode = obj.optString("presentation_mode", "standard"),
                integrityDigest = declaredDigest
            )
        }
    }
}

/**
 * Handles atomic file persistence for continuity checkpoints on Fire OS / Android TV.
 */
class ContinuityCheckpointStore(private val storageDir: File) {

    private val checkpointFile = File(storageDir, "continuity_checkpoint.json")

    fun saveAtomic(checkpoint: ContinuityCheckpoint) {
        if (!storageDir.exists()) {
            storageDir.mkdirs()
        }
        val tempFile = File(storageDir, "continuity_checkpoint.tmp." + UUID.randomUUID())
        FileOutputStream(tempFile).use { fos ->
            val bytes = checkpoint.toJsonObject().toString(2).toByteArray(StandardCharsets.UTF_8)
            fos.write(bytes)
            fos.flush()
            fos.fd.sync()
        }
        if (!tempFile.renameTo(checkpointFile)) {
            // Never overwrite the last committed checkpoint with a partial copy.
            // The temporary file is in the same directory; failed rename is an
            // I/O failure, not permission to weaken the atomic-write contract.
            tempFile.delete()
            throw java.io.IOException("Atomic checkpoint replacement failed")
        }
    }

    fun load(): ContinuityCheckpoint? {
        if (!checkpointFile.exists()) return null
        return try {
            val jsonStr = checkpointFile.readText(StandardCharsets.UTF_8)
            ContinuityCheckpoint.fromJsonObject(JSONObject(jsonStr))
        } catch (e: Exception) {
            // Corrupt or tampered checkpoint -> fail closed (return null)
            null
        }
    }

    fun hasStoredCheckpoint(): Boolean = checkpointFile.exists()

    fun clear() {
        if (checkpointFile.exists()) {
            checkpointFile.delete()
        }
    }
}

