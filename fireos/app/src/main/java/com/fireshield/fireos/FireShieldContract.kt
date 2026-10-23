package com.fireshield.fireos

import org.json.JSONArray
import org.json.JSONObject
import java.math.BigInteger
import java.nio.charset.StandardCharsets
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.NoSuchAlgorithmException
import java.security.Signature
import java.security.spec.X509EncodedKeySpec

enum class FireShieldState {
    UNREVIEWED,
    ARMED,
    INTERVENING,
    RESOLVED,
    ERROR
}

enum class PlayerActionType {
    PAUSE,
    SEEK,
    CONTINUE,
    HOLD,
    ERROR
}

data class PlaybackSessionDescriptor(
    val tenantId: String,
    val assetRevisionId: String,
    val renditionSetId: String,
    val renditionPlaylistSha256: String,
    val timebase: String = "presentation_ms",
    val allowAutoSeekOnPauseOrSkip: Boolean = false
)

data class HazardInterval(
    val intervalId: String,
    val startMs: Long,
    val endMs: Long,
    val safeResumeMs: Long,
    val types: List<String>,
    val policy: String,
    val contextCueIds: List<String> = emptyList(),
    val contextNote: String? = null
)

data class PlayerCommand(
    val action: PlayerActionType,
    val state: FireShieldState,
    val presentationMs: Long,
    val reasonCode: String,
    val requiresSurfaceBlanking: Boolean,
    val targetSeekMs: Long? = null,
    val intervalId: String? = null,
    val hazardTypes: List<String> = emptyList(),
    val contextCueIds: List<String> = emptyList(),
    val contextNote: String? = null
)

data class ValidatedSidecarContract(
    val schemaVersion: Int,
    val signingAlg: String,
    val keyId: String,
    val tenantId: String,
    val assetRevisionId: String,
    val renditionSetId: String,
    val renditionPlaylistSha256: String,
    val detectorVersion: String,
    val reviewState: String,
    val intervals: List<HazardInterval>,
    val manifestDigest: String
)

class FireShieldContractException(message: String) : Exception(message)

object FireShieldContractValidator {
    private val SHA256_REGEX = Regex("^sha256:[0-9a-f]{64}$")
    private val ED25519_SIG_REGEX = Regex("^ed25519:[0-9a-f]{128}$")
    private val ALLOWED_HAZARD_TYPES = setOf("luminance_flash", "saturated_red", "spatial_pattern")
    private val ALLOWED_POLICIES = setOf(
        "pause_before_interval",
        "pause_or_skip",
        "seek_to_safe_resume",
        "hold_playback"
    )
    private val ALLOWED_REVIEW_STATES = setOf("approved", "rejected", "pending_review", "unreviewed")

    // RFC 8410 ASN.1 DER SubjectPublicKeyInfo prefix for a 32-byte raw Ed25519 public key (OID 1.3.101.112)
    private val ED25519_X509_PREFIX = byteArrayOf(
        0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00
    )

    const val SUPPORTED_SCHEMA_VERSION = 2
    const val SUPPORTED_SIGNING_ALG = "Ed25519"
    const val SUPPORTED_TIMEBASE = "presentation_ms"
    const val DEFAULT_MIN_SAFE_RESUME_MARGIN_MS = 200L

    fun sha256Prefixed(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return "sha256:" + digest.joinToString("") { "%02x".format(it) }
    }

    fun hexToBytes(hex: String): ByteArray {
        if (hex.length % 2 != 0) {
            throw FireShieldContractException("Odd-length hex string.")
        }
        return ByteArray(hex.length / 2) { i ->
            val idx = i * 2
            hex.substring(idx, idx + 2).toInt(16).toByte()
        }
    }

    /**
     * Escapes a string strictly according to RFC 8259 without forward-slash escaping,
     * emitting Unicode characters directly as UTF-8, exactly matching Python's
     * json.dumps(..., separators=(',', ':'), ensure_ascii=False).
     */
    fun canonicalQuote(s: String): String {
        val sb = StringBuilder(s.length + 8)
        sb.append('"')
        for (i in 0 until s.length) {
            val c = s[i]
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> {
                    if (c.code < 0x20) {
                        sb.append(String.format(java.util.Locale.US, "\\u%04x", c.code))
                    } else {
                        sb.append(c)
                    }
                }
            }
        }
        sb.append('"')
        return sb.toString()
    }

    /**
     * Produces canonical compact JSON with lexicographically sorted object keys,
     * matching Python's json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False).
     */
    fun canonicalizeJsonValue(value: Any?): String {
        return when (value) {
            null, JSONObject.NULL -> "null"
            is Boolean -> if (value) "true" else "false"
            is Int, is Long -> value.toString()
            is Number -> {
                val d = value.toDouble()
                if (d.isNaN() || d.isInfinite()) {
                    throw FireShieldContractException("Non-finite numbers (NaN/Infinity) are not allowed in canonical JSON.")
                }
                if (d == d.toLong().toDouble()) d.toLong().toString() else value.toString()
            }
            is String -> canonicalQuote(value)
            is JSONObject -> {
                val keys = value.keys().asSequence().toList().sorted()
                val members = keys.map { k ->
                    "${canonicalQuote(k)}:${canonicalizeJsonValue(value.opt(k))}"
                }
                "{${members.joinToString(",")}}"
            }
            is JSONArray -> {
                val elements = (0 until value.length()).map { idx ->
                    canonicalizeJsonValue(value.opt(idx))
                }
                "[${elements.joinToString(",")}]"
            }
            else -> throw FireShieldContractException("Unsupported JSON value type: ${value::class.java.name}")
        }
    }

    fun computeCanonicalManifestDigest(rootObj: JSONObject): String {
        val copy = JSONObject()
        val keys = rootObj.keys().asSequence().toList()
        for (k in keys) {
            if (k != "manifest_digest" && k != "signature") {
                copy.put(k, rootObj.get(k))
            }
        }
        val canonicalJson = canonicalizeJsonValue(copy)
        return sha256Prefixed(canonicalJson.toByteArray(StandardCharsets.UTF_8))
    }

    fun verifyEd25519Signature(
        publicKeyHex: String,
        schemaVersion: Int,
        signingAlg: String,
        keyId: String,
        manifestDigest: String,
        signatureField: String
    ): Boolean {
        val cleanPubHex = publicKeyHex.removePrefix("ed25519-pub:")
        val rawPubBytes = hexToBytes(cleanPubHex)
        if (rawPubBytes.size != 32) {
            throw FireShieldContractException("Ed25519 public key must be exactly 32 bytes.")
        }
        if (!ED25519_SIG_REGEX.matches(signatureField)) {
            throw FireShieldContractException("Malformed Ed25519 signature format.")
        }
        val sigBytes = hexToBytes(signatureField.removePrefix("ed25519:"))
        val message = "fireshield-sidecar:v$schemaVersion:$signingAlg:$keyId:$manifestDigest"
            .toByteArray(StandardCharsets.UTF_8)

        return try {
            val x509Bytes = ED25519_X509_PREFIX + rawPubBytes
            val keyFactory = KeyFactory.getInstance("Ed25519")
            val pubKey = keyFactory.generatePublic(X509EncodedKeySpec(x509Bytes))
            val verifier = Signature.getInstance("Ed25519")
            verifier.initVerify(pubKey)
            verifier.update(message)
            verifier.verify(sigBytes)
        } catch (e: NoSuchAlgorithmException) {
            // Android API < 33 platform fallback: verified pure-Kotlin RFC 8032 engine
            Ed25519CompatVerifier.verify(rawPubBytes, message, sigBytes)
        }
    }

    private fun requireStrictLong(obj: JSONObject, key: String): Long {
        val raw = obj.opt(key)
            ?: throw FireShieldContractException("Missing integer field '$key'.")
        if (raw is Boolean || raw is Double || raw is Float || raw is String) {
            throw FireShieldContractException("Field '$key' must be a strict integer, got ${raw::class.java.simpleName}.")
        }
        if (raw is Int) return raw.toLong()
        if (raw is Long) return raw
        throw FireShieldContractException("Field '$key' must be a strict integer.")
    }

    fun validate(
        sidecarJsonStr: String,
        session: PlaybackSessionDescriptor,
        trustedPublicKeysHex: Map<String, String>,
        revokedKeyIds: Set<String> = emptySet(),
        minSafeResumeMarginMs: Long = DEFAULT_MIN_SAFE_RESUME_MARGIN_MS
    ): ValidatedSidecarContract {
        if (sidecarJsonStr.isBlank()) {
            throw FireShieldContractException("Sidecar manifest string is empty.")
        }
        val root = try {
            JSONObject(sidecarJsonStr)
        } catch (e: Exception) {
            throw FireShieldContractException("Malformed sidecar JSON: ${e.message}")
        }

        val requiredFields = listOf(
            "schema_version",
            "tenant_id",
            "asset_revision_id",
            "rendition_set_id",
            "rendition_playlist_sha256",
            "timebase",
            "detector_version",
            "review_state",
            "manifest_digest"
        )
        for (field in requiredFields) {
            if (!root.has(field) || root.isNull(field)) {
                throw FireShieldContractException("Missing required sidecar field '$field'.")
            }
        }

        val schemaVersionLong = requireStrictLong(root, "schema_version")
        if (schemaVersionLong == 1L) {
            throw FireShieldContractException(
                "Downgrade to legacy schema_version=1 (HMAC) rejected in production Fire OS verifier."
            )
        }
        if (schemaVersionLong != SUPPORTED_SCHEMA_VERSION.toLong()) {
            throw FireShieldContractException("Unsupported schema_version=$schemaVersionLong.")
        }
        val schemaVersion = schemaVersionLong.toInt()

        if (!root.has("signing_alg") || !root.has("key_id") || !root.has("signature")) {
            throw FireShieldContractException("Missing v2 signing fields (signing_alg, key_id, signature).")
        }
        val signingAlg = root.optString("signing_alg", "")
        if (signingAlg != SUPPORTED_SIGNING_ALG) {
            throw FireShieldContractException("Unsupported signing_alg='$signingAlg' (expected Ed25519).")
        }
        val keyId = root.optString("key_id", "")
        if (keyId.isBlank()) {
            throw FireShieldContractException("Field 'key_id' must be non-empty.")
        }

        val tenantId = root.optString("tenant_id", "")
        if (tenantId != session.tenantId) {
            throw FireShieldContractException("Tenant mismatch: '$tenantId' != '${session.tenantId}'.")
        }

        val assetRev = root.optString("asset_revision_id", "")
        if (!SHA256_REGEX.matches(assetRev) || assetRev != session.assetRevisionId) {
            throw FireShieldContractException("asset_revision_id mismatch or invalid sha256.")
        }

        val renditionSetId = root.optString("rendition_set_id", "")
        if (renditionSetId.isBlank() || renditionSetId != session.renditionSetId) {
            throw FireShieldContractException("Stale or mismatched rendition_set_id.")
        }

        val playlistSha = root.optString("rendition_playlist_sha256", "")
        if (!SHA256_REGEX.matches(playlistSha) || playlistSha != session.renditionPlaylistSha256) {
            throw FireShieldContractException("Stale rendition_playlist_sha256 after re-encode or edit.")
        }

        val timebase = root.optString("timebase", "")
        if (timebase != SUPPORTED_TIMEBASE || session.timebase != SUPPORTED_TIMEBASE) {
            throw FireShieldContractException("Unsupported timebase '$timebase'.")
        }

        val detectorVersion = root.optString("detector_version", "")
        if (detectorVersion.isBlank()) {
            throw FireShieldContractException("detector_version must be non-empty.")
        }

        var reviewState = root.optString("review_state", "")
        if (reviewState !in ALLOWED_REVIEW_STATES) {
            throw FireShieldContractException("Invalid review_state '$reviewState'.")
        }

        val declaredDigest = root.optString("manifest_digest", "")
        if (!SHA256_REGEX.matches(declaredDigest)) {
            throw FireShieldContractException("Invalid manifest_digest format.")
        }
        val computedDigest = computeCanonicalManifestDigest(root)
        if (!MessageDigest.isEqual(
                declaredDigest.toByteArray(StandardCharsets.UTF_8),
                computedDigest.toByteArray(StandardCharsets.UTF_8)
            )
        ) {
            throw FireShieldContractException(
                "manifest_digest integrity mismatch: declared=$declaredDigest vs computed=$computedDigest"
            )
        }

        val signatureField = root.optString("signature", "")
        if (signatureField.startsWith("hmac-sha256:")) {
            throw FireShieldContractException("Algorithm downgrade: hmac-sha256 rejected on v2 manifest.")
        }
        if (keyId in revokedKeyIds) {
            throw FireShieldContractException("Publisher key_id='$keyId' has been explicitly revoked.")
        }
        val pubKeyHex = trustedPublicKeysHex[keyId]
            ?: throw FireShieldContractException("Unknown or untrusted publisher key_id='$keyId'.")

        val sigValid = try {
            verifyEd25519Signature(
                publicKeyHex = pubKeyHex,
                schemaVersion = schemaVersion,
                signingAlg = signingAlg,
                keyId = keyId,
                manifestDigest = declaredDigest,
                signatureField = signatureField
            )
        } catch (e: FireShieldContractException) {
            throw e
        } catch (e: Exception) {
            throw FireShieldContractException("Ed25519 verification error: ${e.message}")
        }
        if (!sigValid) {
            throw FireShieldContractException("Ed25519 signature verification failed for key_id='$keyId'.")
        }

        val hasIntervals = root.has("intervals") && !root.isNull("intervals")
        val zeroAttested = root.opt("zero_findings_attested") == true
        val intervalsArray = if (!hasIntervals) {
            if (reviewState == "approved" && zeroAttested) {
                JSONArray()
            } else if (reviewState == "unreviewed" || reviewState == "pending_review") {
                JSONArray()
            } else {
                throw FireShieldContractException(
                    "Absent 'intervals' requires review_state=='approved' with zero_findings_attested=true."
                )
            }
        } else {
            root.optJSONArray("intervals")
                ?: throw FireShieldContractException("Field 'intervals' must be a JSON array.")
        }

        if (intervalsArray.length() == 0 && reviewState == "approved" && !zeroAttested) {
            reviewState = "unreviewed"
        }

        val validatedIntervals = mutableListOf<HazardInterval>()
        var prevEndMs = -1L
        var prevSafeResumeMs = -1L

        for (i in 0 until intervalsArray.length()) {
            val item = intervalsArray.optJSONObject(i)
                ?: throw FireShieldContractException("Interval at index $i must be a JSON object.")

            val startMs = requireStrictLong(item, "start_ms")
            val endMs = requireStrictLong(item, "end_ms")
            val safeResumeMs = requireStrictLong(item, "safe_resume_ms")

            if (startMs < 0L) {
                throw FireShieldContractException("Interval $i has negative start_ms=$startMs.")
            }
            if (endMs < startMs) {
                throw FireShieldContractException("Interval $i has end_ms ($endMs) < start_ms ($startMs).")
            }
            if (safeResumeMs < endMs + minSafeResumeMarginMs) {
                throw FireShieldContractException(
                    "Interval $i violates safe_resume_ms margin ($safeResumeMs < $endMs + $minSafeResumeMarginMs)."
                )
            }
            if (i > 0) {
                if (startMs <= prevEndMs) {
                    throw FireShieldContractException("Unsorted or overlapping intervals at index $i.")
                }
                if (startMs < prevSafeResumeMs) {
                    throw FireShieldContractException(
                        "Interval $i starts at $startMs before previous safe_resume_ms=$prevSafeResumeMs."
                    )
                }
            }

            val typesArr = item.optJSONArray("types")
                ?: throw FireShieldContractException("Interval $i missing 'types' array.")
            if (typesArr.length() == 0) {
                throw FireShieldContractException("Interval $i 'types' array must be non-empty.")
            }
            val typesList = mutableListOf<String>()
            for (tIdx in 0 until typesArr.length()) {
                val tVal = typesArr.optString(tIdx, "")
                if (tVal !in ALLOWED_HAZARD_TYPES) {
                    throw FireShieldContractException("Interval $i has unsupported hazard type '$tVal'.")
                }
                typesList.add(tVal)
            }

            val policy = item.optString("policy", "")
            if (policy !in ALLOWED_POLICIES) {
                throw FireShieldContractException("Interval $i has unsupported policy '$policy'.")
            }

            val cuesList = mutableListOf<String>()
            if (item.has("context_cue_ids") && !item.isNull("context_cue_ids")) {
                val cuesArr = item.optJSONArray("context_cue_ids")
                    ?: throw FireShieldContractException("Interval $i invalid context_cue_ids.")
                for (cIdx in 0 until cuesArr.length()) {
                    val cue = cuesArr.opt(cIdx)
                    if (cue !is String || cue.isBlank()) {
                        throw FireShieldContractException("Interval $i invalid cue entry.")
                    }
                    cuesList.add(cue)
                }
            }

            val intervalId = item.optString("interval_id", "interval-%03d".format(i))
            if (intervalId.isBlank()) {
                throw FireShieldContractException("Interval $i has blank interval_id.")
            }

            val noteObj = item.optJSONObject("context_note")
            val noteText = when {
                noteObj != null -> if (noteObj.has("text")) noteObj.optString("text") else null
                item.has("context_note") && !item.isNull("context_note") -> item.optString("context_note")
                else -> null
            }

            validatedIntervals.add(
                HazardInterval(
                    intervalId = intervalId,
                    startMs = startMs,
                    endMs = endMs,
                    safeResumeMs = safeResumeMs,
                    types = typesList,
                    policy = policy,
                    contextCueIds = cuesList,
                    contextNote = noteText
                )
            )
            prevEndMs = endMs
            prevSafeResumeMs = safeResumeMs
        }

        return ValidatedSidecarContract(
            schemaVersion = schemaVersion,
            signingAlg = signingAlg,
            keyId = keyId,
            tenantId = tenantId,
            assetRevisionId = assetRev,
            renditionSetId = renditionSetId,
            renditionPlaylistSha256 = playlistSha,
            detectorVersion = detectorVersion,
            reviewState = reviewState,
            intervals = validatedIntervals,
            manifestDigest = declaredDigest
        )
    }
}

/**
 * Self-contained, pure-Kotlin RFC 8032 Ed25519 signature verification engine.
 * Provides fallback compatibility for Android API levels 26-32 where the platform
 * Conscrypt JCA provider lacks "Ed25519" Signature support (introduced in API 33).
 */
object Ed25519CompatVerifier {
    private val Q = BigInteger.valueOf(2).pow(255).subtract(BigInteger.valueOf(19))
    private val L = BigInteger.valueOf(2).pow(252).add(BigInteger("27742317777372353535851937790883648493"))
    private val D = BigInteger("-121665").multiply(BigInteger("121666").modInverse(Q)).mod(Q)
    private val I = BigInteger.valueOf(2).modPow(Q.subtract(BigInteger.ONE).divide(BigInteger.valueOf(4)), Q)

    data class Point(val x: BigInteger, val y: BigInteger)

    private val ZERO_POINT = Point(BigInteger.ZERO, BigInteger.ONE)
    private val BASE_POINT: Point by lazy {
        val by = BigInteger.valueOf(4).multiply(BigInteger.valueOf(5).modInverse(Q)).mod(Q)
        val bx = recoverX(by, 0) ?: error("Failed to compute Ed25519 base point")
        Point(bx, by)
    }

    private fun recoverX(y: BigInteger, signBit: Int): BigInteger? {
        if (y >= Q) return null
        val y2 = y.multiply(y).mod(Q)
        val u = y2.subtract(BigInteger.ONE).mod(Q)
        val v = D.multiply(y2).add(BigInteger.ONE).mod(Q)
        val xx = u.multiply(v.modInverse(Q)).mod(Q)
        if (xx == BigInteger.ZERO) {
            return if (signBit == 0) BigInteger.ZERO else null
        }
        var x = xx.modPow(Q.add(BigInteger.valueOf(3)).divide(BigInteger.valueOf(8)), Q)
        if (x.multiply(x).subtract(xx).mod(Q) != BigInteger.ZERO) {
            x = x.multiply(I).mod(Q)
        }
        if (x.multiply(x).subtract(xx).mod(Q) != BigInteger.ZERO) return null
        if (x.testBit(0) != (signBit == 1)) {
            x = Q.subtract(x)
        }
        return x
    }

    private fun decodePoint(bytes: ByteArray): Point? {
        if (bytes.size != 32) return null
        var y = BigInteger.ZERO
        for (i in 0 until 32) {
            val b = bytes[i].toInt() and 0xFF
            y = y.or(BigInteger.valueOf(b.toLong()).shiftLeft(i * 8))
        }
        val signBit = if (y.testBit(255)) 1 else 0
        y = y.clearBit(255)
        val x = recoverX(y, signBit) ?: return null
        return Point(x, y)
    }

    private fun pointAdd(p1: Point, p2: Point): Point {
        val (x1, y1) = p1
        val (x2, y2) = p2
        val x1x2 = x1.multiply(x2).mod(Q)
        val y1y2 = y1.multiply(y2).mod(Q)
        val x1y2 = x1.multiply(y2).mod(Q)
        val y1x2 = y1.multiply(x2).mod(Q)
        val dxxyy = D.multiply(x1x2).multiply(y1y2).mod(Q)

        val x3 = x1y2.add(y1x2).multiply(BigInteger.ONE.add(dxxyy).modInverse(Q)).mod(Q)
        val y3 = y1y2.add(x1x2).multiply(BigInteger.ONE.subtract(dxxyy).modInverse(Q)).mod(Q)
        return Point(x3, y3)
    }

    private fun scalarMul(s: BigInteger, p: Point): Point {
        var result = ZERO_POINT
        var base = p
        for (i in 0 until 256) {
            if (s.testBit(i)) {
                result = pointAdd(result, base)
            }
            base = pointAdd(base, base)
        }
        return result
    }

    private fun bytesToBigIntLittleEndian(bytes: ByteArray, offset: Int, length: Int): BigInteger {
        var res = BigInteger.ZERO
        for (i in 0 until length) {
            val b = bytes[offset + i].toInt() and 0xFF
            res = res.or(BigInteger.valueOf(b.toLong()).shiftLeft(i * 8))
        }
        return res
    }

    fun verify(publicKeyBytes: ByteArray, messageBytes: ByteArray, signatureBytes: ByteArray): Boolean {
        if (publicKeyBytes.size != 32 || signatureBytes.size != 64) return false

        val aPoint = decodePoint(publicKeyBytes) ?: return false
        val rBytes = signatureBytes.copyOfRange(0, 32)
        val rPoint = decodePoint(rBytes) ?: return false
        val s = bytesToBigIntLittleEndian(signatureBytes, 32, 32)
        if (s >= L) return false

        val md = MessageDigest.getInstance("SHA-512")
        md.update(rBytes)
        md.update(publicKeyBytes)
        md.update(messageBytes)
        val h = md.digest()
        val k = bytesToBigIntLittleEndian(h, 0, 64).mod(L)

        val left = scalarMul(BigInteger.valueOf(8).multiply(s), BASE_POINT)
        val rightPart1 = scalarMul(BigInteger.valueOf(8), rPoint)
        val rightPart2 = scalarMul(BigInteger.valueOf(8).multiply(k), aPoint)
        val right = pointAdd(rightPart1, rightPart2)

        return left == right
    }
}
