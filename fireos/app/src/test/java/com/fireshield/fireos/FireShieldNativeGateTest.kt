package com.fireshield.fireos

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.File

class InMemoryNativePlayerPort : NativeExoPlayerPort {
    var currentPosMs: Long = 0L
    var paused: Boolean = false
    var shutterBlanked: Boolean = false
    var bannerVisible: Boolean = false
    var lastBannerReason: String = ""
    var submittedSeeks: MutableList<Pair<Long, Long>> = mutableListOf()

    override fun getCurrentPositionMs(): Long = currentPosMs

    override fun isPlaying(): Boolean = !paused

    override fun pause() {
        paused = true
    }

    override fun play() {
        paused = false
    }

    override fun seekToMsAsync(targetMs: Long, seekToken: Long): Boolean {
        if (targetMs < 0L) return false
        submittedSeeks.add(targetMs to seekToken)
        return true
    }

    override fun setShutterSurfaceBlanked(blanked: Boolean) {
        shutterBlanked = blanked
    }

    override fun showInterventionBanner(
        reasonCode: String,
        intervalId: String?,
        contextCueIds: List<String>,
        targetSeekMs: Long?
    ) {
        bannerVisible = true
        lastBannerReason = reasonCode
    }

    override fun hideInterventionBanner() {
        bannerVisible = false
    }
}

class FireShieldNativeGateTest {

    private lateinit var sampleSidecarJson: String
    private lateinit var provenanceJson: JSONObject
    private lateinit var validSession: PlaybackSessionDescriptor
    private lateinit var trustedKeys: Map<String, String>

    @Before
    fun setUp() {
        val assetsDir = File("src/main/assets/hls").let {
            if (it.exists()) it else File("app/src/main/assets/hls")
        }
        sampleSidecarJson = File(assetsDir, "sample_sidecar_v2.json").readText(Charsets.UTF_8)
        provenanceJson = JSONObject(File(assetsDir, "PROVENANCE.json").readText(Charsets.UTF_8))

        validSession = PlaybackSessionDescriptor(
            tenantId = provenanceJson.getString("tenant_id"),
            assetRevisionId = provenanceJson.getString("asset_revision_id"),
            renditionSetId = provenanceJson.getString("rendition_set_id"),
            renditionPlaylistSha256 = provenanceJson.getString("rendition_playlist_sha256")
        )
        trustedKeys = mapOf(
            provenanceJson.getString("key_id") to provenanceJson.getString("public_key_hex")
        )
    }

    @Test
    fun testValidEd25519V2SidecarFromPythonPipelineVerifiesInKotlin() {
        val contract = FireShieldContractValidator.validate(
            sidecarJsonStr = sampleSidecarJson,
            session = validSession,
            trustedPublicKeysHex = trustedKeys
        )
        assertEquals(2, contract.schemaVersion)
        assertEquals("Ed25519", contract.signingAlg)
        assertEquals("publisher-ed25519-key-2026-q3", contract.keyId)
        assertEquals("approved", contract.reviewState)
        assertEquals(1, contract.intervals.size)
        assertEquals(967L, contract.intervals[0].startMs)
        assertEquals(2033L, contract.intervals[0].endMs)
        assertEquals(2233L, contract.intervals[0].safeResumeMs)
    }

    @Test
    fun testRejectsLegacySchemaVersion1HmacDowngrade() {
        val root = JSONObject(sampleSidecarJson)
        root.put("schema_version", 1)
        try {
            FireShieldContractValidator.validate(root.toString(), validSession, trustedKeys)
            fail("Expected FireShieldContractException on schema_version=1 downgrade")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("schema_version=1"))
        }
    }

    @Test
    fun testRejectsUnknownKeyIdAndTamperedPayload() {
        // 1. Unknown key_id
        try {
            FireShieldContractValidator.validate(
                sampleSidecarJson,
                validSession,
                mapOf("other-key-id" to trustedKeys.values.first())
            )
            fail("Expected unknown key_id rejection")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("Unknown or untrusted publisher key_id"))
        }

        // 2. Tampered interval start_ms without updating digest/signature
        val root = JSONObject(sampleSidecarJson)
        root.getJSONArray("intervals").getJSONObject(0).put("start_ms", 1200L)
        try {
            FireShieldContractValidator.validate(root.toString(), validSession, trustedKeys)
            fail("Expected manifest_digest integrity mismatch")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("manifest_digest integrity mismatch"))
        }

        // 3. Recomputed manifest_digest after tampering, but original signature
        val tamperedDigest = FireShieldContractValidator.computeCanonicalManifestDigest(root)
        root.put("manifest_digest", tamperedDigest)
        try {
            FireShieldContractValidator.validate(root.toString(), validSession, trustedKeys)
            fail("Expected Ed25519 signature verification failure")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("Ed25519 signature verification failed"))
        }
    }

    @Test
    fun testRejectsStalePlaylistHashAfterReencode() {
        val staleSession = validSession.copy(
            renditionPlaylistSha256 = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        )
        try {
            FireShieldContractValidator.validate(sampleSidecarJson, staleSession, trustedKeys)
            fail("Expected stale rendition_playlist_sha256 rejection")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("Stale rendition_playlist_sha256"))
        }
    }

    @Test
    fun testAsyncPlayerGateKeepsShutterBlankedDuringInFlightSeekUntilDiscontinuityCallback() {
        val port = InMemoryNativePlayerPort()
        val bridge = FireShieldPlayerGateBridge(
            player = port,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys,
            preGuardMs = 500L,
            seekTimeoutMs = 3000L
        )

        // Safe playback before pre-guard window (hazard starts at 967ms, preGuard=500ms -> 467ms)
        val cmdSafe = bridge.onPlaybackTick(presentationMs = 200L, nowMonotonicMs = 1000L)
        assertEquals(FireShieldState.ARMED, cmdSafe.state)
        assertEquals(PlayerActionType.CONTINUE, cmdSafe.action)
        assertFalse(port.paused)
        assertFalse(port.shutterBlanked)

        // Entering pre-guard window at 500ms -> must PAUSE and BLANK shutter
        val cmdGuard = bridge.onPlaybackTick(presentationMs = 500L, nowMonotonicMs = 1300L)
        assertEquals(FireShieldState.INTERVENING, cmdGuard.state)
        assertEquals(PlayerActionType.PAUSE, cmdGuard.action)
        assertTrue(port.paused)
        assertTrue(port.shutterBlanked)
        assertTrue(port.bannerVisible)

        // Viewer confirms bypass seek -> issues async seek, MUST remain paused & blanked while in-flight
        val cmdSeek = bridge.requestViewerBypassSeek(nowMonotonicMs = 1500L)
        assertEquals(FireShieldState.INTERVENING, cmdSeek.state)
        assertEquals(PlayerActionType.SEEK, cmdSeek.action)
        assertEquals(2233L, cmdSeek.targetSeekMs)
        assertTrue(port.paused)
        assertTrue(port.shutterBlanked)
        assertEquals(1, port.submittedSeeks.size)
        val (seekTarget, seekToken) = port.submittedSeeks.first()
        assertEquals(2233L, seekTarget)
        assertEquals(1L, seekToken)

        // Playback tick while seek is still in flight -> stays paused and blanked
        val cmdInFlight = bridge.onPlaybackTick(presentationMs = 550L, nowMonotonicMs = 1700L)
        assertEquals("AWAITING_ASYNC_SEEK_COMPLETION_HOLD", cmdInFlight.reasonCode)
        assertTrue(port.paused)
        assertTrue(port.shutterBlanked)

        // Position discontinuity callback confirms landing >= safe_resume_ms (2233ms)
        val cmdResolved = bridge.onPositionDiscontinuitySeekCompleted(
            seekToken = seekToken,
            actualPositionMs = 2233L,
            nowMonotonicMs = 1850L,
            eventId = "disc-event-1"
        )
        assertEquals(FireShieldState.RESOLVED, cmdResolved.state)
        assertEquals(PlayerActionType.CONTINUE, cmdResolved.action)
        assertFalse(port.paused)
        assertFalse(port.shutterBlanked)
        assertFalse(port.bannerVisible)
    }

    @Test
    fun testAsyncSeekFailsClosedOnShortLandingStaleTokenReplayAndTimeout() {
        // 1. Short landing (< safe_resume_ms)
        val port1 = InMemoryNativePlayerPort()
        val bridge1 = FireShieldPlayerGateBridge(
            player = port1,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys
        )
        bridge1.onPlaybackTick(presentationMs = 600L, nowMonotonicMs = 1000L)
        bridge1.requestViewerBypassSeek(nowMonotonicMs = 1100L)
        val shortCmd = bridge1.onPositionDiscontinuitySeekCompleted(
            seekToken = 1L,
            actualPositionMs = 1800L, // Inside hazard window (< 2233ms)
            nowMonotonicMs = 1200L,
            eventId = "short-1"
        )
        assertEquals(FireShieldState.ERROR, shortCmd.state)
        assertTrue(port1.paused)
        assertTrue(port1.shutterBlanked)

        // 2. Stale / mismatched seek token
        val port2 = InMemoryNativePlayerPort()
        val bridge2 = FireShieldPlayerGateBridge(
            player = port2,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys
        )
        bridge2.onPlaybackTick(presentationMs = 600L, nowMonotonicMs = 1000L)
        bridge2.requestViewerBypassSeek(nowMonotonicMs = 1100L)
        val staleTokenCmd = bridge2.onPositionDiscontinuitySeekCompleted(
            seekToken = 9999L,
            actualPositionMs = 2233L,
            nowMonotonicMs = 1200L,
            eventId = "stale-token-1"
        )
        assertEquals(FireShieldState.ERROR, staleTokenCmd.state)
        assertTrue(port2.paused)
        assertTrue(port2.shutterBlanked)

        // 3. Seek timeout on playback tick
        val port3 = InMemoryNativePlayerPort()
        val bridge3 = FireShieldPlayerGateBridge(
            player = port3,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys,
            seekTimeoutMs = 2000L
        )
        bridge3.onPlaybackTick(presentationMs = 600L, nowMonotonicMs = 1000L)
        bridge3.requestViewerBypassSeek(nowMonotonicMs = 1100L)
        val timeoutCmd = bridge3.onPlaybackTick(presentationMs = 600L, nowMonotonicMs = 3500L)
        assertEquals(FireShieldState.ERROR, timeoutCmd.state)
        assertTrue(timeoutCmd.reasonCode.contains("IN_FLIGHT_SEEK_TIMED_OUT"))
        assertTrue(port3.paused)
        assertTrue(port3.shutterBlanked)

        // 4. Replayed discontinuity event ID
        val port4 = InMemoryNativePlayerPort()
        val bridge4 = FireShieldPlayerGateBridge(
            player = port4,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys
        )
        bridge4.onPlaybackTick(presentationMs = 600L, nowMonotonicMs = 1000L)
        bridge4.requestViewerBypassSeek(nowMonotonicMs = 1100L)
        val okCmd = bridge4.onPositionDiscontinuitySeekCompleted(
            seekToken = 1L,
            actualPositionMs = 2233L,
            nowMonotonicMs = 1200L,
            eventId = "replay-evt-1"
        )
        assertEquals(FireShieldState.RESOLVED, okCmd.state)
        val replayCmd = bridge4.onPositionDiscontinuitySeekCompleted(
            seekToken = 1L,
            actualPositionMs = 2233L,
            nowMonotonicMs = 1300L,
            eventId = "replay-evt-1"
        )
        assertEquals(FireShieldState.ERROR, replayCmd.state)
        assertTrue(port4.paused)
        assertTrue(port4.shutterBlanked)
    }

    @Test
    fun testKeyRotationRevocationAndAuditSinkEmission() {
        val port = InMemoryNativePlayerPort()
        val capturedAudit = mutableListOf<FireShieldAuditEvent>()
        val bridge = FireShieldPlayerGateBridge(
            player = port,
            session = validSession,
            sidecarJsonStr = sampleSidecarJson,
            trustedPublicKeysHex = trustedKeys + mapOf("publisher-ed25519-key-2026-q4" to trustedKeys.values.first()),
            auditSink = { evt -> capturedAudit.add(evt) }
        )

        assertEquals(FireShieldState.ARMED, bridge.state)
        assertEquals("publisher-ed25519-key-2026-q3", bridge.activeKeyId)
        assertTrue(capturedAudit.any { it.reasonCode == "CONTRACT_VERIFIED_ARMED" && it.tenantId == validSession.tenantId })

        // Revoking the active Q3 key immediately fails closed, pauses, and blanks shutter
        val cmdRevoked = bridge.updateKeyRing(
            newTrustedPublicKeysHex = trustedKeys,
            newRevokedKeyIds = setOf("publisher-ed25519-key-2026-q3"),
            nowMonotonicMs = 2000L
        )
        assertEquals(FireShieldState.ERROR, cmdRevoked.state)
        assertEquals(PlayerActionType.ERROR, cmdRevoked.action)
        assertTrue(cmdRevoked.requiresSurfaceBlanking)
        assertTrue(port.paused)
        assertTrue(port.shutterBlanked)
        assertTrue(capturedAudit.any { it.reasonCode.contains("revoked") })
    }

    @Test
    fun testEd25519CompatVerifierDirectlyAndTamperRejection() {
        val pubBytes = FireShieldContractValidator.hexToBytes(provenanceJson.getString("public_key_hex"))
        val root = JSONObject(sampleSidecarJson)
        val manifestDigest = root.getString("manifest_digest")
        val sigBytes = FireShieldContractValidator.hexToBytes(root.getString("signature").removePrefix("ed25519:"))
        val message = "fireshield-sidecar:v2:Ed25519:publisher-ed25519-key-2026-q3:$manifestDigest"
            .toByteArray(Charsets.UTF_8)

        // 1. Positive verification using pure-Kotlin RFC 8032 verifier
        val valid = Ed25519CompatVerifier.verify(pubBytes, message, sigBytes)
        assertTrue("Ed25519CompatVerifier should verify valid Python signature", valid)

        // 2. Tamper message -> must reject (false)
        val tamperedMessage = "fireshield-sidecar:v2:Ed25519:publisher-ed25519-key-2026-q3:sha256:0000000000000000000000000000000000000000000000000000000000000000"
            .toByteArray(Charsets.UTF_8)
        val tamperedMsgResult = Ed25519CompatVerifier.verify(pubBytes, tamperedMessage, sigBytes)
        assertFalse("Ed25519CompatVerifier must reject tampered message", tamperedMsgResult)

        // 3. Tamper signature -> must reject (false)
        val tamperedSig = sigBytes.copyOf()
        tamperedSig[0] = (tamperedSig[0] + 1).toByte()
        val tamperedSigResult = Ed25519CompatVerifier.verify(pubBytes, message, tamperedSig)
        assertFalse("Ed25519CompatVerifier must reject tampered signature", tamperedSigResult)
    }

    @Test
    fun testContinuityCheckpointPersistenceAndTamperDetection() {
        val tempDir = File(System.getProperty("java.io.tmpdir"), "fireshield_test_" + System.currentTimeMillis())
        tempDir.mkdirs()
        try {
            val store = ContinuityCheckpointStore(tempDir)
            val cp = ContinuityCheckpoint.create(
                assetRevisionId = validSession.assetRevisionId,
                renditionSetId = validSession.renditionSetId,
                renditionPlaylistSha256 = validSession.renditionPlaylistSha256,
                positionMs = 45000L,
                captionsEnabled = true,
                selectedSubtitleTrackId = "sub-en",
                presentationMode = "reduced_effects"
            )
            store.saveAtomic(cp)

            // Valid load
            val loaded = store.load()
            assertTrue("Loaded checkpoint should not be null", loaded != null)
            assertEquals(45000L, loaded!!.positionMs)
            assertTrue(loaded.captionsEnabled)
            assertEquals("sub-en", loaded.selectedSubtitleTrackId)
            assertEquals(cp.integrityDigest, loaded.integrityDigest)

            // Tamper with checkpoint file
            val file = File(tempDir, "continuity_checkpoint.json")
            val raw = JSONObject(file.readText(Charsets.UTF_8))
            raw.put("position_ms", 99999L)
            file.writeText(raw.toString(2), Charsets.UTF_8)

            // Tampered load fails closed (returns null)
            val tampered = store.load()
            assertTrue("Tampered checkpoint must return null (fail closed)", tampered == null)
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun testRecoveryVerifierDetectsDroppedCaptionsAndCutDrift() {
        val cp = ContinuityCheckpoint.create(
            assetRevisionId = validSession.assetRevisionId,
            renditionSetId = validSession.renditionSetId,
            renditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            positionMs = 50000L,
            captionsEnabled = true,
            selectedSubtitleTrackId = "sub-en",
            presentationMode = "reduced_effects"
        )

        // 1. Happy path: landing within tolerance (50300ms, diff=300ms <= 1500ms), captions active
        val observedOk = ObservedPlayerState(
            positionMs = 50300L,
            captionsActive = true,
            activeSubtitleTrackId = "sub-en",
            activeAudioTrackId = null,
            activePresentationMode = "reduced_effects",
            activeRenditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            surfaceBlanked = false,
            isPlaying = true
        )
        val resOk = RecoveryVerifier.verify(cp, observedOk)
        assertEquals("VERIFIED_MATCH", resOk.status)
        assertTrue(resOk.mismatchFields.isEmpty())

        // 2. Dropped captions defect
        val observedDroppedCaps = observedOk.copy(captionsActive = false, activeSubtitleTrackId = null)
        val resDropped = RecoveryVerifier.verify(cp, observedDroppedCaps)
        assertEquals("DIVERGENCE_DETECTED", resDropped.status)
        assertTrue(resDropped.mismatchFields.contains("captions_enabled"))

        // 3. Cut drift / playlist digest mismatch
        val observedDrift = observedOk.copy(activeRenditionPlaylistSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000")
        val resDrift = RecoveryVerifier.verify(cp, observedDrift)
        assertEquals("DIVERGENCE_DETECTED", resDrift.status)
        assertTrue(resDrift.mismatchFields.contains("rendition_playlist_sha256"))

        // 4. Generate incident receipt with canonical digest
        val receipt = RecoveryVerifier.generateIncidentReceipt(
            incidentId = "inc-native-001",
            fixtureId = "DROPPED_CAPTION_FAULT_V1",
            verificationResult = resDropped
        )
        assertEquals(1, receipt.getInt("receipt_schema_version"))
        assertEquals("inc-native-001", receipt.getString("incident_id"))
        assertEquals("DIVERGENCE_DETECTED", receipt.getString("recovery_status"))
        assertEquals("DROPPED_CAPTION_FAULT_V1", receipt.getString("retest_fixture_id"))
        assertTrue(RecoveryVerifier.validateReceiptDigest(receipt))

        val evidenceDir = File("../../evidence/2026-10-15-run3").let {
            if (it.exists()) it else File("../evidence/2026-10-15-run3").let { d ->
                if (d.exists()) d else File("evidence/2026-10-15-run3")
            }
        }
        if (evidenceDir.exists()) {
            File(evidenceDir, "actual_native_receipt.json").writeText(receipt.toString(2), Charsets.UTF_8)
        }

        assertFalse(receipt.getJSONObject("privacy_attestation").getBoolean("contains_pii"))
        assertFalse(receipt.getJSONObject("privacy_attestation").getBoolean("contains_video_buffers"))

        // Hostile string rejection
        try {
            RecoveryVerifier.sanitizeReceiptString("<script>alert(1)</script>")
            fail("Expected exception on hostile string in receipt")
        } catch (e: FireShieldContractException) {
            assertTrue(e.message!!.contains("Hostile"))
        }
    }

    @Test
    fun testValidBenignSidecarContractVerifiesInKotlin() {
        val assetsDir = File("src/main/assets/hls").let {
            if (it.exists()) it else File("app/src/main/assets/hls")
        }
        val benignSidecar = File(assetsDir, "benign_sidecar_v2.json").readText(Charsets.UTF_8)
        val benignProv = JSONObject(File(assetsDir, "PROVENANCE_BENIGN.json").readText(Charsets.UTF_8))

        val benignSession = PlaybackSessionDescriptor(
            tenantId = benignProv.getString("tenant_id"),
            assetRevisionId = benignProv.getString("asset_revision_id"),
            renditionSetId = benignProv.getString("rendition_set_id"),
            renditionPlaylistSha256 = benignProv.getString("rendition_playlist_sha256")
        )
        val benignKeys = mapOf(
            benignProv.getString("key_id") to benignProv.getString("public_key_hex")
        )

        val contract = FireShieldContractValidator.validate(
            sidecarJsonStr = benignSidecar,
            session = benignSession,
            trustedPublicKeysHex = benignKeys
        )
        assertEquals(2, contract.schemaVersion)
        assertEquals("approved", contract.reviewState)
        assertEquals(0, contract.intervals.size)
        assertEquals(benignProv.getString("asset_revision_id"), contract.assetRevisionId)
        assertEquals(benignProv.getString("rendition_playlist_sha256"), contract.renditionPlaylistSha256)
    }

    @Test
    fun testRecoveryVerifierFailsClosedOnMissingOrMismatchedAudio() {
        val cp = ContinuityCheckpoint.create(
            assetRevisionId = validSession.assetRevisionId,
            renditionSetId = validSession.renditionSetId,
            renditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            positionMs = 30000L,
            captionsEnabled = true,
            selectedSubtitleTrackId = "sub-en",
            selectedAudioTrackId = "audio-en-stereo",
            presentationMode = "standard"
        )

        // 1. Missing required audio (observed activeAudioTrackId is null) -> must fail closed
        val observedMissingAudio = ObservedPlayerState(
            positionMs = 30200L,
            captionsActive = true,
            activeSubtitleTrackId = "sub-en",
            activeAudioTrackId = null, // Missing!
            activePresentationMode = "standard",
            activeRenditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            surfaceBlanked = false,
            isPlaying = true
        )
        val resMissing = RecoveryVerifier.verify(cp, observedMissingAudio)
        assertEquals("DIVERGENCE_DETECTED", resMissing.status)
        assertTrue(resMissing.mismatchFields.contains("selected_audio_track_id"))

        // 2. Mismatched audio language/channel
        val observedWrongAudio = observedMissingAudio.copy(activeAudioTrackId = "audio-es-stereo")
        val resWrong = RecoveryVerifier.verify(cp, observedWrongAudio)
        assertEquals("DIVERGENCE_DETECTED", resWrong.status)
        assertTrue(resWrong.mismatchFields.contains("selected_audio_track_id"))

        // 3. Audio matching exactly -> pass
        val observedMatchingAudio = observedMissingAudio.copy(activeAudioTrackId = "audio-en-stereo")
        val resMatching = RecoveryVerifier.verify(cp, observedMatchingAudio)
        assertEquals("VERIFIED_MATCH", resMatching.status)
        assertTrue(resMatching.mismatchFields.isEmpty())
    }

    @Test
    fun testReceiptDigestIntegrityAndScenarioMapping() {
        val cp = ContinuityCheckpoint.create(
            assetRevisionId = validSession.assetRevisionId,
            renditionSetId = validSession.renditionSetId,
            renditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            positionMs = 40000L,
            captionsEnabled = true,
            selectedSubtitleTrackId = "sub-en",
            selectedAudioTrackId = "audio-en",
            presentationMode = "standard"
        )

        // Test audio mismatch mapping -> DROPPED_AUDIO_FAULT_V1
        val obsAudioLoss = ObservedPlayerState(
            positionMs = 40100L,
            captionsActive = true,
            activeSubtitleTrackId = "sub-en",
            activeAudioTrackId = null,
            activePresentationMode = "standard",
            activeRenditionPlaylistSha256 = validSession.renditionPlaylistSha256,
            surfaceBlanked = false,
            isPlaying = true
        )
        val resAudio = RecoveryVerifier.verify(cp, obsAudioLoss)
        val receiptAudio = RecoveryVerifier.generateIncidentReceipt(
            fixtureId = "test_audio_fixture",
            verificationResult = resAudio
        )
        assertEquals("DROPPED_AUDIO_FAULT_V1", receiptAudio.getString("retest_fixture_id"))
        assertTrue(RecoveryVerifier.validateReceiptDigest(receiptAudio))
        val evidenceDir = File("../../evidence/2026-10-15-run3").let {
            if (it.exists()) it else File("../evidence/2026-10-15-run3").let { d ->
                if (d.exists()) d else File("evidence/2026-10-15-run3")
            }
        }
        if (evidenceDir.exists()) {
            File(evidenceDir, "actual_native_audio_receipt.json").writeText(receiptAudio.toString(2), Charsets.UTF_8)
        }

        // Tamper with receipt -> digest validation must fail
        val tamperedReceipt = JSONObject(receiptAudio.toString())
        tamperedReceipt.put("position_delta_ms", 88888L)
        assertFalse(RecoveryVerifier.validateReceiptDigest(tamperedReceipt))

        // Test position overshoot mapping -> POSITION_OVERSHOOT_FAULT_V1
        val obsOvershoot = obsAudioLoss.copy(
            activeAudioTrackId = "audio-en",
            positionMs = 45000L // +5000ms overshoot
        )
        val resOver = RecoveryVerifier.verify(cp, obsOvershoot)
        val receiptOver = RecoveryVerifier.generateIncidentReceipt(
            fixtureId = "test_over_fixture",
            verificationResult = resOver
        )
        assertEquals("POSITION_OVERSHOOT_FAULT_V1", receiptOver.getString("retest_fixture_id"))
        assertTrue(RecoveryVerifier.validateReceiptDigest(receiptOver))

        // Test stale cut mapping -> STALE_CUT_INTRO_DRIFT_V1
        val obsDrift = obsAudioLoss.copy(
            activeAudioTrackId = "audio-en",
            activeRenditionPlaylistSha256 = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        )
        val resDrift = RecoveryVerifier.verify(cp, obsDrift)
        val receiptDrift = RecoveryVerifier.generateIncidentReceipt(
            fixtureId = "test_drift_fixture",
            verificationResult = resDrift
        )
        assertEquals("STALE_CUT_INTRO_DRIFT_V1", receiptDrift.getString("retest_fixture_id"))
        assertTrue(RecoveryVerifier.validateReceiptDigest(receiptDrift))
    }

    @Test
    fun testEd25519CompatVerifierRejectsRFC8032NegativeVectors() {
        val pubBytes = FireShieldContractValidator.hexToBytes(provenanceJson.getString("public_key_hex"))
        val root = JSONObject(sampleSidecarJson)
        val manifestDigest = root.getString("manifest_digest")
        val sigBytes = FireShieldContractValidator.hexToBytes(root.getString("signature").removePrefix("ed25519:"))
        val message = "fireshield-sidecar:v2:Ed25519:publisher-ed25519-key-2026-q3:$manifestDigest"
            .toByteArray(Charsets.UTF_8)

        // 1. Invalid signature length (!= 64 bytes)
        assertFalse(Ed25519CompatVerifier.verify(pubBytes, message, ByteArray(63)))
        assertFalse(Ed25519CompatVerifier.verify(pubBytes, message, ByteArray(65)))

        // 2. Invalid public key length (!= 32 bytes)
        assertFalse(Ed25519CompatVerifier.verify(ByteArray(31), message, sigBytes))
        assertFalse(Ed25519CompatVerifier.verify(ByteArray(33), message, sigBytes))

        // 3. Scalar s >= L (RFC 8032: s must be strictly less than order L = 2^252 + 27742317777372353535851937790883648493)
        val invalidSBytes = sigBytes.copyOf()
        // Set higher bits of S so s >= L
        invalidSBytes[63] = 0xFF.toByte()
        invalidSBytes[62] = 0xFF.toByte()
        assertFalse("Ed25519CompatVerifier must reject s >= L per RFC 8032", Ed25519CompatVerifier.verify(pubBytes, message, invalidSBytes))

        // 4. Invalid point encoding (y >= p = 2^255 - 19)
        val invalidPointPub = pubBytes.copyOf()
        // Setting y to 2^255 - 1 makes y >= p
        invalidPointPub[31] = 0x7F.toByte() // clear sign bit, leave highest bits set
        for (i in 0 until 31) invalidPointPub[i] = 0xFF.toByte()
        assertFalse("Ed25519CompatVerifier must reject point with y >= 2^255-19", Ed25519CompatVerifier.verify(invalidPointPub, message, sigBytes))
    }

    @Test
    fun testCanonicalJsonCrossLanguageParityVectors() {
        val vectorsFile = File("../../tools/canonical_vectors.json").let {
            if (it.exists()) it else File("../tools/canonical_vectors.json").let { f2 ->
                if (f2.exists()) f2 else File("src/test/resources/canonical_vectors.json")
            }
        }
        assertTrue("canonical_vectors.json must exist", vectorsFile.exists())
        val jsonArray = org.json.JSONArray(vectorsFile.readText(Charsets.UTF_8))
        assertTrue("Vectors array must not be empty", jsonArray.length() > 0)

        for (i in 0 until jsonArray.length()) {
            val vector = jsonArray.getJSONObject(i)
            val name = vector.getString("name")
            val rawInput = vector.get("input")
            val expectedCanonical = vector.getString("expected_canonical")
            val expectedSha = vector.getString("expected_sha256")

            val actualCanonical = FireShieldContractValidator.canonicalizeJsonValue(rawInput)
            assertEquals("Canonical JSON mismatch for vector '$name'", expectedCanonical, actualCanonical)

            val actualSha = FireShieldContractValidator.sha256Prefixed(actualCanonical.toByteArray(Charsets.UTF_8))
            assertEquals("Canonical SHA-256 mismatch for vector '$name'", expectedSha, actualSha)
        }
    }
}




