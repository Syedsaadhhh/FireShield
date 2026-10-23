package com.fireshield.fireos

import android.app.Activity
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.res.AssetManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.common.PlaybackException
import androidx.media3.common.Tracks
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import org.json.JSONObject
import java.io.File

/**
 * Runtime-configurable B2B playback harness configuration.
 * Can be injected via Android Intent extras (e.g. `adb shell am start --es ...`)
 * on an Android TV emulator or Fire OS device without recompiling, or falls back
 * to the bundled publisher-owned synthetic benign HLS fixture in assets/hls/.
 */
data class FireShieldHarnessConfig(
    val session: PlaybackSessionDescriptor,
    val mediaUri: String,
    val sidecarJsonStr: String,
    val trustedPublicKeysHex: Map<String, String>,
    val revokedKeyIds: Set<String>,
    val preGuardMs: Long,
    val seekTimeoutMs: Long,
    val presentationMode: String = "standard"
) {
    companion object {
        fun fromIntentOrAssets(suppliedIntent: Intent?, assets: AssetManager, allowDebugOverrides: Boolean = false): FireShieldHarnessConfig {
            // External launcher callers cannot replace the release app's trust root.
            val intent = if (allowDebugOverrides) suppliedIntent else null
            val releaseFilePath = intent?.getStringExtra("fireshield.release_file")
            val releaseJson = if (!releaseFilePath.isNullOrBlank()) {
                val f = File(releaseFilePath)
                if (f.exists()) JSONObject(f.readText(Charsets.UTF_8)) else null
            } else null

            val provenanceAsset = intent?.getStringExtra("fireshield.provenance_asset_path")
                ?: "hls/PROVENANCE_BENIGN.json"
            val provenanceJson = JSONObject(
                assets.open(provenanceAsset).bufferedReader().use { it.readText() }
            )

            val playlistAsset = intent?.getStringExtra("fireshield.playlist_asset_path")
                ?: "hls/benign_variant.m3u8"
            val defaultPlaylistSha = assets.open(playlistAsset).use {
                FireShieldContractValidator.sha256Prefixed(it.readBytes())
            }

            val tenantId = intent?.getStringExtra("fireshield.tenant_id")
                ?: releaseJson?.optString("tenant_id")
                ?: provenanceJson.getString("tenant_id")
            val assetRev = intent?.getStringExtra("fireshield.asset_revision_id")
                ?: releaseJson?.optString("asset_revision_id")
                ?: provenanceJson.getString("asset_revision_id")
            val renditionSetId = intent?.getStringExtra("fireshield.rendition_set_id")
                ?: releaseJson?.optString("rendition_set_id")
                ?: provenanceJson.getString("rendition_set_id")
            val playlistSha = intent?.getStringExtra("fireshield.playlist_sha256")
                ?: releaseJson?.optString("rendition_playlist_sha256")
                ?: defaultPlaylistSha

            val mediaUri = intent?.getStringExtra("fireshield.media_uri")
                ?: releaseJson?.optString("media_uri")
                ?: "asset:///$playlistAsset"

            var effectivePlaylistSha = playlistSha
            // Validate loaded media/playlist bytes against the expected signed playlistSha when accessible
            if (mediaUri.startsWith("asset:///")) {
                val relAssetPath = mediaUri.removePrefix("asset:///")
                try {
                    val actualBytes = assets.open(relAssetPath).use { it.readBytes() }
                    val actualSha = FireShieldContractValidator.sha256Prefixed(actualBytes)
                    if (actualSha != playlistSha) {
                        Log.w("FireShieldMedia", "Asset playlist sha mismatch: actual=$actualSha vs expected=$playlistSha")
                    }
                    effectivePlaylistSha = actualSha
                } catch (e: Exception) {
                    Log.w("FireShieldMedia", "Could not read asset playlist bytes for validation: ${e.message}")
                    effectivePlaylistSha = "sha256:unreadable-asset-bytes"
                }
            } else if (mediaUri.startsWith("/") || mediaUri.startsWith("file://")) {
                val filePath = mediaUri.removePrefix("file://")
                val file = File(filePath)
                if (file.exists()) {
                    val actualSha = FireShieldContractValidator.sha256Prefixed(file.readBytes())
                    if (actualSha != playlistSha) {
                        Log.w("FireShieldMedia", "File playlist sha mismatch: actual=$actualSha vs expected=$playlistSha")
                    }
                    effectivePlaylistSha = actualSha
                } else {
                    effectivePlaylistSha = "sha256:missing-file-media"
                }
            }

            val sidecarAsset = intent?.getStringExtra("fireshield.sidecar_asset_path")
                ?: "hls/benign_sidecar_v2.json"
            val sidecarStr = intent?.getStringExtra("fireshield.sidecar_json_inline")
                ?: releaseJson?.optString("sidecar_json")
                ?: releaseJson?.optJSONObject("sidecar_contract")?.toString()
                ?: assets.open(sidecarAsset).bufferedReader().use { it.readText() }

            val trustedKeys = mutableMapOf<String, String>()
            val trustedKeysJsonStr = intent?.getStringExtra("fireshield.trusted_keys_json")
            if (!trustedKeysJsonStr.isNullOrBlank()) {
                val obj = JSONObject(trustedKeysJsonStr)
                for (k in obj.keys()) {
                    trustedKeys[k] = obj.getString(k)
                }
            } else if (releaseJson != null && releaseJson.has("public_verification_metadata")) {
                val meta = releaseJson.getJSONObject("public_verification_metadata")
                trustedKeys[meta.getString("key_id")] = meta.getString("public_key_hex")
            } else {
                trustedKeys[provenanceJson.getString("key_id")] = provenanceJson.getString("public_key_hex")
            }

            val revokedCsv = intent?.getStringExtra("fireshield.revoked_key_ids_csv") ?: ""
            val revokedSet = revokedCsv.split(",")
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .toSet()

            val preGuardMs = intent?.getLongExtra("fireshield.pre_guard_ms", 500L) ?: 500L
            val seekTimeoutMs = intent?.getLongExtra("fireshield.seek_timeout_ms", 3000L) ?: 3000L

            return FireShieldHarnessConfig(
                session = PlaybackSessionDescriptor(
                    tenantId = tenantId,
                    assetRevisionId = assetRev,
                    renditionSetId = renditionSetId,
                    renditionPlaylistSha256 = effectivePlaylistSha
                ),
                mediaUri = mediaUri,
                sidecarJsonStr = sidecarStr,
                trustedPublicKeysHex = trustedKeys,
                revokedKeyIds = revokedSet,
                preGuardMs = preGuardMs,
                seekTimeoutMs = seekTimeoutMs,
                presentationMode = provenanceJson.optString("presentation_mode", "standard")
            )
        }
    }
}

/**
 * Functional diagnostic harness Activity for Fire OS / Android TV (Media3 ExoPlayer).
 *
 * Implements:
 * 1. Stopped-Activity lifecycle (onStop resource release, atomic checkpointing).
 * 2. Version-bound checkpoint creation & cold-launch recovery.
 * 3. Actual track selection inspection via Player.currentTracks.
 * 4. Controlled fault injection triggers for TV testing.
 * 5. Allowlisted incident receipt generation and display.
 * 6. Explicitly treats onPositionDiscontinuity as a position notification,
 *    not proof of rendered frame output.
 */
class MainActivity : Activity() {

    private var exoPlayer: ExoPlayer? = null
    private var gateBridge: FireShieldPlayerGateBridge? = null
    private var controllerPort: Media3ExoPlayerController? = null
    private var activeConfig: FireShieldHarnessConfig? = null
    private lateinit var checkpointStore: ContinuityCheckpointStore
    private lateinit var playerView: PlayerView
    private lateinit var statusTextView: TextView
    private lateinit var verificationTextView: TextView
    private lateinit var shutterOverlay: View
    private val mainHandler = Handler(Looper.getMainLooper())
    private var discontinuitySeq = 0L

    // Committed Presentation Policy (Preserved viewer intent, distinct from transient observations)
    private var committedCaptionsEnabled = true
    private var committedSubtitleTrackId: String? = null
    private var committedAudioTrackId: String? = null
    private var committedPresentationMode = "standard"
    private var lastCheckpointWriteMonotonicMs = 0L

    // Recovery & Verification Gate State
    private var isRecoveryInFlight = false
    private var recoveryDeadlineMonotonicMs = 0L
    private var isDivergenceLatched = false
    private var activeIncidentReceipt: JSONObject? = null
    private val recoveryReadiness = RecoveryReadinessGate()
    private var recoveryOperationToken = 0L
    private var pendingRecoveryCheckpoint: ContinuityCheckpoint? = null
    private var renderedFrameSeen = false
    private var recoveryRequestedPositionMs = 0L

    private val pollRunnable = object : Runnable {
        override fun run() {
            val bridge = gateBridge
            val player = exoPlayer
            val nowMs = SystemClock.elapsedRealtime()

            if (isDivergenceLatched) {
                // Authoritative fail-closed latch: keep paused and blanked
                player?.pause()
                shutterOverlay.visibility = View.VISIBLE
                mainHandler.postDelayed(this, 200L)
                return
            }

            if (isRecoveryInFlight) {
                // Timeout and readiness are independent of matching position/track fields.
                triggerRecoveryVerificationCheck()
                mainHandler.postDelayed(this, 200L)
                return
            }

            if (bridge != null && player != null) {
                val cmd = bridge.onPlaybackTick(
                    presentationMs = player.currentPosition,
                    nowMonotonicMs = nowMs
                )
                updateDiagnosticReadout(cmd)
                // Periodically persist checkpoint during normal playback (rate-limited to 1s)
                if (cmd.state == FireShieldState.ARMED || cmd.state == FireShieldState.RESOLVED) {
                    saveCurrentPlaybackCheckpoint(nowMonotonicMs = nowMs, force = false)
                }
            }
            mainHandler.postDelayed(this, 200L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        checkpointStore = ContinuityCheckpointStore(File(filesDir, "fireshield_checkpoints"))

        val rootLayout = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
        }

        playerView = PlayerView(this).apply {
            useController = false
            subtitleView?.setBottomPaddingFraction(0.28f)
        }
        rootLayout.addView(
            playerView,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )

        shutterOverlay = View(this).apply {
            setBackgroundColor(Color.BLACK)
            visibility = View.VISIBLE
        }
        rootLayout.addView(
            shutterOverlay,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )

        val density = resources.displayMetrics.density
        val dp48 = (48 * density).toInt()
        val dp27 = (27 * density).toInt()
        val dp16 = (16 * density).toInt()
        val dp8 = (8 * density).toInt()

        // Top TV Header Bar (TV Safe Margins: 48dp horizontal, 27dp top)
        val headerBar = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp48, dp27, dp48, dp8)
            setBackgroundColor(Color.argb(190, 10, 14, 22))
        }
        val headerTitle = TextView(this).apply {
            text = "FireShield Stream Protection · Fire OS / Media3"
            setTextColor(Color.WHITE)
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
        }
        headerBar.addView(headerTitle)

        val headerSubtitle = TextView(this).apply {
            text = "Deterministic SDR Photo-screening & Continuity Gate"
            setTextColor(Color.rgb(148, 163, 184))
            textSize = 13f
        }
        headerBar.addView(headerSubtitle)

        rootLayout.addView(
            headerBar,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        )

        // Bottom HUD Bar (TV Safe Margins: 48dp horizontal, 27dp bottom)
        val controlsColumn = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp48, dp16, dp48, dp27)
            setBackgroundColor(Color.argb(230, 13, 16, 23))
        }

        statusTextView = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            text = "FireTrace Synaptic Continuity Harness Initializing..."
        }
        controlsColumn.addView(statusTextView)

        verificationTextView = TextView(this).apply {
            setTextColor(Color.rgb(251, 191, 36)) // Amber-Yellow
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
            setPadding(0, dp8, 0, dp16)
            text = "Recovery Verification: Awaiting Checkpoint Assessment"
        }
        controlsColumn.addView(verificationTextView)

        // Helper to style TV focusable buttons
        fun styleTvButton(btn: Button, isSecondary: Boolean = false) {
            val normalBg = if (isSecondary) Color.argb(220, 30, 41, 59) else Color.argb(240, 45, 55, 75)
            val focusedBg = Color.rgb(217, 119, 6) // Amber focus
            btn.setTextColor(Color.WHITE)
            btn.textSize = 14f
            btn.typeface = Typeface.DEFAULT_BOLD
            btn.isFocusable = true
            btn.isFocusableInTouchMode = true
            btn.setPadding(24, 14, 24, 14)
            btn.setBackgroundColor(normalBg)
            btn.onFocusChangeListener = View.OnFocusChangeListener { _, hasFocus ->
                btn.setBackgroundColor(if (hasFocus) focusedBg else normalBg)
                btn.scaleX = if (hasFocus) 1.05f else 1.0f
                btn.scaleY = if (hasFocus) 1.05f else 1.0f
            }
        }

        // Primary Viewer Controls Row
        val primaryButtonRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }

        val bypassBtn = Button(this).apply {
            text = "Bypass Seek"
            styleTvButton(this)
            setOnClickListener {
                if (isRecoveryInFlight || isDivergenceLatched) return@setOnClickListener
                val cmd = gateBridge?.requestViewerBypassSeek(SystemClock.elapsedRealtime())
                if (cmd != null) updateDiagnosticReadout(cmd)
            }
        }
        primaryButtonRow.addView(bypassBtn)

        val toggleSubsBtn = Button(this).apply {
            text = "Toggle Subs"
            styleTvButton(this)
            setOnClickListener {
                if (isRecoveryInFlight || isDivergenceLatched) return@setOnClickListener
                committedCaptionsEnabled = !committedCaptionsEnabled
                committedSubtitleTrackId = null
                applySubtitleTrackSelection(committedCaptionsEnabled)
                saveCurrentPlaybackCheckpoint(force = true)
            }
        }
        val toggleParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        primaryButtonRow.addView(toggleSubsBtn, toggleParams)

        val resetBtn = Button(this).apply {
            text = "Reset Harness"
            styleTvButton(this)
            setOnClickListener { resetHarness() }
        }
        val resetParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        primaryButtonRow.addView(resetBtn, resetParams)

        // Collapsible Diagnostic Fault Section
        val diagnosticContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(0, dp16, 0, 0)
        }

        val diagnosticHeader = TextView(this).apply {
            text = "Diagnostic Retest Fault Injections (Simulates Discontinuity & Mismatch):"
            setTextColor(Color.rgb(148, 163, 184))
            textSize = 12f
            setPadding(0, 0, 0, dp8)
        }
        diagnosticContainer.addView(diagnosticHeader)

        val faultButtonRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }

        val dropSubsFaultBtn = Button(this).apply {
            text = "Fault: Drop Subs"
            styleTvButton(this, isSecondary = true)
            setOnClickListener { injectDroppedTrackFault(C.TRACK_TYPE_TEXT) }
        }
        faultButtonRow.addView(dropSubsFaultBtn)

        val dropAudioFaultBtn = Button(this).apply {
            text = "Fault: Drop Audio"
            styleTvButton(this, isSecondary = true)
            setOnClickListener { injectDroppedTrackFault(C.TRACK_TYPE_AUDIO) }
        }
        val audioParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        faultButtonRow.addView(dropAudioFaultBtn, audioParams)

        val staleCutBtn = Button(this).apply {
            text = "Fault: Cut Drift"
            styleTvButton(this, isSecondary = true)
            setOnClickListener { injectCutDriftFault() }
        }
        val cutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        faultButtonRow.addView(staleCutBtn, cutParams)

        val simulateRecreationBtn = Button(this).apply {
            text = "Simulate Cold Recreation"
            styleTvButton(this, isSecondary = true)
            setOnClickListener {
                saveCurrentPlaybackCheckpoint(force = true)
                releasePlayer()
                initializeExoPlayerAndGate(playerView)
            }
        }
        val simParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        faultButtonRow.addView(simulateRecreationBtn, simParams)

        diagnosticContainer.addView(faultButtonRow)

        // Toggle button for diagnostic container
        val toggleDiagnosticBtn = Button(this).apply {
            text = "Diagnostic Controls ▼"
            styleTvButton(this, isSecondary = true)
            setOnClickListener {
                if (diagnosticContainer.visibility == View.VISIBLE) {
                    diagnosticContainer.visibility = View.GONE
                    text = "Diagnostic Controls ▼"
                } else {
                    diagnosticContainer.visibility = View.VISIBLE
                    text = "Diagnostic Controls ▲"
                }
            }
        }
        val diagToggleParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { marginStart = dp8 }
        primaryButtonRow.addView(toggleDiagnosticBtn, diagToggleParams)

        val primaryScroll = ScrollView(this).apply {
            isFillViewport = true
        }
        primaryScroll.addView(primaryButtonRow)
        controlsColumn.addView(primaryScroll)
        controlsColumn.addView(diagnosticContainer)

        rootLayout.addView(
            controlsColumn,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        )

        setContentView(rootLayout)
        initializeExoPlayerAndGate(playerView)
    }

    private fun initializeExoPlayerAndGate(playerView: PlayerView) {
        val player = ExoPlayer.Builder(this).build()
        exoPlayer = player
        renderedFrameSeen = false
        isRecoveryInFlight = false
        isDivergenceLatched = false
        pendingRecoveryCheckpoint = null
        recoveryReadiness.clear()
        playerView.player = player

        val cfg = FireShieldHarnessConfig.fromIntentOrAssets(intent, assets,
            allowDebugOverrides = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        activeConfig = cfg

        val port = Media3ExoPlayerController(
            exoPlayer = player,
            playerView = playerView,
            explicitShutterOverlay = shutterOverlay
        ) { visible, bannerMsg ->
            statusTextView.text = "BannerVisible=$visible | $bannerMsg"
        }
        controllerPort = port

        val bridge = try {
            FireShieldPlayerGateBridge(
                player = port,
                session = cfg.session,
                sidecarJsonStr = cfg.sidecarJsonStr,
                trustedPublicKeysHex = cfg.trustedPublicKeysHex,
                revokedKeyIds = cfg.revokedKeyIds,
                preGuardMs = cfg.preGuardMs,
                seekTimeoutMs = cfg.seekTimeoutMs
            ) { auditEvent ->
                Log.i(
                    "FireShieldAudit",
                    "seq=${auditEvent.seq} tenant=${auditEvent.tenantId} rev=${auditEvent.assetRevisionId} " +
                        "rend=${auditEvent.renditionSetId} state=${auditEvent.state} posMs=${auditEvent.presentationMs}"
                )
            }
        } catch (e: Exception) {
            Log.e("FireShieldContract", "Contract validation failed: ${e.message}", e)
            isDivergenceLatched = true
            shutterOverlay.visibility = View.VISIBLE
            statusTextView.text = "FAIL-CLOSED: Contract validation failed: ${e.message}"
            verificationTextView.text = "Playback blocked by FireShield Contract Gate"
            player.pause()
            null
        }
        gateBridge = bridge

        player.addListener(object : Player.Listener {
            override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo, newPosition: Player.PositionInfo, reason: Int) {
                if (exoPlayer !== player || reason != Player.DISCONTINUITY_REASON_SEEK) return
                if (isRecoveryInFlight) {
                    // Recovery seeks do not have a hazard-bypass token.
                    if (kotlin.math.abs(newPosition.positionMs - recoveryRequestedPositionMs) <= RecoveryVerifier.DEFAULT_POSITION_TOLERANCE_MS) {
                        recoveryReadiness.onSeekConfirmed(recoveryOperationToken, SystemClock.elapsedRealtime())
                        triggerRecoveryVerificationCheck()
                    }
                } else {
                    discontinuitySeq += 1L
                    val cmd = bridge?.onPositionDiscontinuitySeekCompleted(
                        seekToken = port.getLastSubmittedSeekToken(),
                        actualPositionMs = newPosition.positionMs,
                        nowMonotonicMs = SystemClock.elapsedRealtime(),
                        eventId = "exo-seek-disc-$discontinuitySeq"
                    )
                    if (cmd != null) updateDiagnosticReadout(cmd)
                }
            }

            override fun onTracksChanged(tracks: Tracks) {
                if (exoPlayer !== player) return
                if (!isRecoveryInFlight && !isDivergenceLatched) {
                    if (committedAudioTrackId == null) committedAudioTrackId = getSelectedAudioTrackId(player)
                    if (committedCaptionsEnabled && committedSubtitleTrackId == null) {
                        committedSubtitleTrackId = getSelectedSubtitleTrackId(player)
                    }
                }
                if (isRecoveryInFlight) {
                    recoveryReadiness.onTracksKnown(recoveryOperationToken, tracks.groups.isNotEmpty(), SystemClock.elapsedRealtime())
                    triggerRecoveryVerificationCheck()
                }
            }

            override fun onPlaybackStateChanged(playbackState: Int) {
                if (exoPlayer !== player) return
                if (isRecoveryInFlight) {
                    val now = SystemClock.elapsedRealtime()
                    if (playbackState == Player.STATE_READY) {
                        if (kotlin.math.abs(player.currentPosition - recoveryRequestedPositionMs) <= RecoveryVerifier.DEFAULT_POSITION_TOLERANCE_MS) {
                            recoveryReadiness.onSeekConfirmed(recoveryOperationToken, now)
                        }
                    }
                    recoveryReadiness.onDecoderReady(recoveryOperationToken, playbackState == Player.STATE_READY, now)
                    triggerRecoveryVerificationCheck()
                }
            }

            override fun onRenderedFirstFrame() {
                if (exoPlayer !== player) return
                renderedFrameSeen = true
                Log.i("FireTrace", "FRAME_RENDERED posMs=${player.currentPosition}")
                if (isRecoveryInFlight) {
                    recoveryReadiness.onFrameRendered(recoveryOperationToken, SystemClock.elapsedRealtime())
                    triggerRecoveryVerificationCheck()
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                if (exoPlayer !== player) return
                if (isRecoveryInFlight) {
                    failRecovery("PLAYER_ERROR_" + error.errorCodeName)
                } else {
                    Log.e("FireTrace", "PLAYER_ERROR_" + error.errorCodeName)
                }
            }
        })

        // Build MediaItem with explicit subtitle configuration
        val subtitleConfig = MediaItem.SubtitleConfiguration.Builder(Uri.parse("asset:///hls/subtitles_en.vtt"))
            .setMimeType(MimeTypes.TEXT_VTT)
            .setLanguage("en")
            .setLabel("English CC")
            .setId("sub-en")
            .setSelectionFlags(C.SELECTION_FLAG_DEFAULT)
            .build()

        val mediaItem = MediaItem.Builder()
            .setUri(Uri.parse(cfg.mediaUri))
            .setSubtitleConfigurations(listOf(subtitleConfig))
            .build()

        player.setMediaItem(mediaItem)
        // Arm recovery BEFORE prepare can produce frame/track callbacks.
        checkAndPerformCheckpointRecovery(player, cfg)
        applySubtitleTrackSelection(committedCaptionsEnabled)
        player.prepare()
        mainHandler.removeCallbacks(pollRunnable)
        mainHandler.post(pollRunnable)
    }

    private fun checkAndPerformCheckpointRecovery(player: ExoPlayer, cfg: FireShieldHarnessConfig) {
        val checkpoint = checkpointStore.load()
        if (checkpoint == null) {
            if (checkpointStore.hasStoredCheckpoint()) {
                failRecovery("CORRUPT_CHECKPOINT")
                return
            }
            committedPresentationMode = cfg.presentationMode
            // Fresh playback is still governed by the verified publisher gate.
            return
        }
        if (checkpoint.assetRevisionId != cfg.session.assetRevisionId ||
            checkpoint.renditionSetId != cfg.session.renditionSetId) {
            failRecovery("STALE_ASSET_REVISION")
            return
        }
        committedCaptionsEnabled = checkpoint.captionsEnabled
        committedSubtitleTrackId = checkpoint.selectedSubtitleTrackId
        committedAudioTrackId = checkpoint.selectedAudioTrackId
        committedPresentationMode = checkpoint.presentationMode
        val debugOffset = if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
            intent.getLongExtra("fireshield.test_seek_offset_ms", 0L) else 0L
        val requestedPosition = checkpoint.positionMs + debugOffset
        beginRecoveryEvaluation(checkpoint, seekIsNoOp = player.currentPosition == requestedPosition)
        recoveryRequestedPositionMs = requestedPosition
        applySubtitleTrackSelection(committedCaptionsEnabled)
        player.seekTo(requestedPosition)
        Log.i("FireTrace", "RECOVERY_BEGIN token=$recoveryOperationToken targetMs=${checkpoint.positionMs}")
    }

    private fun beginRecoveryEvaluation(checkpoint: ContinuityCheckpoint, seekIsNoOp: Boolean, reuseCurrentFrame: Boolean = false, tracksAlreadyKnown: Boolean = false) {
        val player = exoPlayer ?: return
        val now = SystemClock.elapsedRealtime()
        pendingRecoveryCheckpoint = checkpoint
        recoveryRequestedPositionMs = player.currentPosition
        recoveryOperationToken += 1L
        isRecoveryInFlight = true
        isDivergenceLatched = false
        val timeoutMs = activeConfig?.seekTimeoutMs ?: 3000L
        recoveryDeadlineMonotonicMs = now + timeoutMs
        recoveryReadiness.begin(recoveryOperationToken, now, timeoutMs, seekIsNoOp)
        player.pause()
        shutterOverlay.visibility = View.VISIBLE
        if (reuseCurrentFrame && renderedFrameSeen) recoveryReadiness.onFrameRendered(recoveryOperationToken, now)
        if (tracksAlreadyKnown) recoveryReadiness.onTracksKnown(recoveryOperationToken, player.currentTracks.groups.isNotEmpty(), now)
        recoveryReadiness.onDecoderReady(recoveryOperationToken, player.playbackState == Player.STATE_READY, now)
        verificationTextView.setTextColor(Color.YELLOW)
        verificationTextView.text = "Recovery In-Flight: awaiting seek, READY, selected tracks and rendered frame"
    }

    private fun failRecovery(reason: String) {
        isRecoveryInFlight = false
        isDivergenceLatched = true
        recoveryReadiness.clear()
        exoPlayer?.pause()
        shutterOverlay.visibility = View.VISIBLE
        verificationTextView.setTextColor(Color.RED)
        verificationTextView.text = "RECOVERY BLOCKED: $reason (paused and blanked)"
        Log.e("FireTrace", "RECOVERY_BLOCKED reason=$reason token=$recoveryOperationToken")
    }

    private fun injectDroppedTrackFault(trackType: Int) {
        if (isRecoveryInFlight || isDivergenceLatched) return
        val player = exoPlayer ?: return
        saveCurrentPlaybackCheckpoint(force = true)
        val checkpoint = checkpointStore.load() ?: return
        beginRecoveryEvaluation(checkpoint, seekIsNoOp = true, reuseCurrentFrame = true)
        player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(trackType, true).build()
    }

    private fun injectCutDriftFault() {
        if (isRecoveryInFlight || isDivergenceLatched) return
        saveCurrentPlaybackCheckpoint(force = true)
        val checkpoint = checkpointStore.load() ?: return
        val bridge = gateBridge ?: return
        val cfg = activeConfig ?: return
        beginRecoveryEvaluation(checkpoint, seekIsNoOp = true, reuseCurrentFrame = true, tracksAlreadyKnown = true)
        val changed = bridge.session.copy(renditionPlaylistSha256 = "sha256:" + "0".repeat(64))
        updateDiagnosticReadout(bridge.onRenditionOrPlaylistChanged(changed, cfg.sidecarJsonStr, SystemClock.elapsedRealtime()))
    }

    private fun resetHarness() {
        checkpointStore.clear()
        isDivergenceLatched = false
        isRecoveryInFlight = false
        pendingRecoveryCheckpoint = null
        recoveryReadiness.clear()
        committedCaptionsEnabled = true
        committedSubtitleTrackId = null
        committedAudioTrackId = null
        activeIncidentReceipt = null
        releasePlayer()
        initializeExoPlayerAndGate(playerView)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Diagnostic controls are enabled only in debuggable builds.
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        when (intent.getStringExtra("fireshield.test_action")) {
            "drop_subtitles" -> injectDroppedTrackFault(C.TRACK_TYPE_TEXT)
            "drop_audio" -> injectDroppedTrackFault(C.TRACK_TYPE_AUDIO)
            "cut_drift" -> injectCutDriftFault()
            "reset" -> resetHarness()
            "recreate_player" -> {
                saveCurrentPlaybackCheckpoint(force = true)
                releasePlayer()
                initializeExoPlayerAndGate(playerView)
            }
            "presentation_mismatch" -> {
                saveCurrentPlaybackCheckpoint(force = true)
                checkpointStore.load()?.let { cp ->
                    val desired = ContinuityCheckpoint.create(
                        assetRevisionId = cp.assetRevisionId, renditionSetId = cp.renditionSetId,
                        renditionPlaylistSha256 = cp.renditionPlaylistSha256, positionMs = cp.positionMs,
                        captionsEnabled = cp.captionsEnabled, selectedSubtitleTrackId = cp.selectedSubtitleTrackId,
                        selectedAudioTrackId = cp.selectedAudioTrackId, presentationMode = "reduced_effects")
                    checkpointStore.saveAtomic(desired)
                    beginRecoveryEvaluation(desired, seekIsNoOp = true, reuseCurrentFrame = true, tracksAlreadyKnown = true)
                }
            }
            "timeout" -> {
                saveCurrentPlaybackCheckpoint(force = true)
                checkpointStore.load()?.let { beginRecoveryEvaluation(it, seekIsNoOp = true) }
            }
        }
    }

    private fun saveCurrentPlaybackCheckpoint(nowMonotonicMs: Long = SystemClock.elapsedRealtime(), force: Boolean = false) {
        val player = exoPlayer ?: return
        val bridge = gateBridge ?: return

        // Freeze checkpoint writes during recovery or when divergence is latched
        if (isRecoveryInFlight || isDivergenceLatched) {
            return
        }

        // Rate-limit periodic writes to once per 1000ms unless forced
        if (!force && (nowMonotonicMs - lastCheckpointWriteMonotonicMs < 1000L)) {
            return
        }
        lastCheckpointWriteMonotonicMs = nowMonotonicMs

        val cp = ContinuityCheckpoint.create(
            assetRevisionId = bridge.session.assetRevisionId,
            renditionSetId = bridge.session.renditionSetId,
            renditionPlaylistSha256 = bridge.session.renditionPlaylistSha256,
            positionMs = player.currentPosition,
            captionsEnabled = committedCaptionsEnabled,
            selectedSubtitleTrackId = if (committedCaptionsEnabled) committedSubtitleTrackId else null,
            selectedAudioTrackId = committedAudioTrackId,
            presentationMode = committedPresentationMode
        )
        checkpointStore.saveAtomic(cp)
    }

    private fun isSubtitleTrackActuallySelected(player: Player): Boolean {
        for (group in player.currentTracks.groups) {
            if (group.type == C.TRACK_TYPE_TEXT && group.isSelected) {
                return true
            }
        }
        return false
    }

    private fun getSelectedSubtitleTrackId(player: Player): String? =
        SelectedTrackIdentity.selectedId(player.currentTracks, C.TRACK_TYPE_TEXT)
    private fun getSelectedAudioTrackId(player: Player): String? =
        SelectedTrackIdentity.selectedId(player.currentTracks, C.TRACK_TYPE_AUDIO)

    private fun applySubtitleTrackSelection(enabled: Boolean) {
        val player = exoPlayer ?: return
        player.trackSelectionParameters = player.trackSelectionParameters
            .buildUpon()
            .setPreferredTextLanguage("en")
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, !enabled)
            .build()
    }

    private fun triggerRecoveryVerificationCheck() {
        val player = exoPlayer ?: return
        val bridge = gateBridge ?: return
        val cp = pendingRecoveryCheckpoint ?: return
        if (!isRecoveryInFlight || isDivergenceLatched) return
        val now = SystemClock.elapsedRealtime()
        when (recoveryReadiness.status(recoveryOperationToken, now)) {
            RecoveryReadinessStatus.TIMED_OUT -> { failRecovery("RECOVERY_READINESS_TIMEOUT"); return }
            RecoveryReadinessStatus.READY_TO_VERIFY -> Unit
            else -> return
        }
        if (player.playbackState != Player.STATE_READY) return

        val observed = ObservedPlayerState(
            positionMs = player.currentPosition,
            captionsActive = isSubtitleTrackActuallySelected(player),
            activeSubtitleTrackId = getSelectedSubtitleTrackId(player),
            activeAudioTrackId = getSelectedAudioTrackId(player),
            // Actual edition policy is derived from the active media configuration, not the checkpoint.
            activePresentationMode = activeConfig?.presentationMode ?: "unsupported",
            activeRenditionPlaylistSha256 = bridge.session.renditionPlaylistSha256,
            surfaceBlanked = shutterOverlay.visibility == View.VISIBLE,
            isPlaying = player.isPlaying
        )

        val result = RecoveryVerifier.verify(cp, observed)
        if (result.status == "VERIFIED_MATCH") {
            if (bridge.state == FireShieldState.ERROR) {
                failRecovery("PUBLISHER_GATE_ERROR")
                return
            }
            isRecoveryInFlight = false
            isDivergenceLatched = false
            recoveryReadiness.clear()
            pendingRecoveryCheckpoint = null
            // Only the publisher gate owns playback release and shutter state.
            updateDiagnosticReadout(bridge.onPlaybackTick(player.currentPosition, SystemClock.elapsedRealtime()))
            Log.i("FireTrace", "RECOVERY_VERIFIED token=$recoveryOperationToken posMs=${observed.positionMs} captions=${observed.captionsActive} audio=${observed.activeAudioTrackId}")

            verificationTextView.setTextColor(Color.GREEN)
            verificationTextView.text = "SELECTION MATCH: pos=${observed.positionMs}ms, captions=${observed.captionsActive}, audio=${observed.activeAudioTrackId ?: "none"}. Output not verified."
        } else {
            // Fail closed: latch divergence, pause player, keep shutter blanked
            isDivergenceLatched = true
            isRecoveryInFlight = false
            recoveryReadiness.clear()
            player.pause()
            shutterOverlay.visibility = View.VISIBLE

            val receipt = RecoveryVerifier.generateIncidentReceipt(
                fixtureId = "benign_calm_presentation_with_subtitles",
                verificationResult = result
            )
            activeIncidentReceipt = receipt
            val shortDigest = receipt.optString("receipt_digest", "").take(16)
            verificationTextView.setTextColor(Color.RED)
            verificationTextView.text = "DIVERGENCE DETECTED [${result.mismatchFields.joinToString(", ")}]: Fail-Closed Paused & Blanked (Digest: $shortDigest...)"
            Log.w("FireTraceReceipt", receipt.toString(2))
        }
    }

    private fun updateDiagnosticReadout(cmd: PlayerCommand) {
        val bridge = gateBridge
        statusTextView.text = buildString {
            append("Tenant=${bridge?.session?.tenantId} | State=${cmd.state} | Action=${cmd.action} | ")
            append("Pos=${cmd.presentationMs}ms | Blank=${cmd.requiresSurfaceBlanking} | ")
            append("Reason=${cmd.reasonCode}")
            if (!cmd.contextNote.isNullOrBlank()) {
                append(" | Note: \"${cmd.contextNote}\"")
            }
        }
    }

    override fun onPause() {
        exoPlayer?.pause()
        super.onPause()
    }

    override fun onStop() {
        // Amazon Multimedia App Requirements:
        // Halt background polling, commit playback state, and release decoder resources in onStop
        mainHandler.removeCallbacks(pollRunnable)
        saveCurrentPlaybackCheckpoint(force = true)
        releasePlayer()
        super.onStop()
    }

    override fun onStart() {
        super.onStart()
        if (exoPlayer == null) {
            initializeExoPlayerAndGate(playerView)
        }
    }

    private fun releasePlayer() {
        mainHandler.removeCallbacks(pollRunnable)
        recoveryReadiness.clear()
        exoPlayer?.release()
        exoPlayer = null
    }

    override fun onDestroy() {
        releasePlayer()
        super.onDestroy()
    }
}
