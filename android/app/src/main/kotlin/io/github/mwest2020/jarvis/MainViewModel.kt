package io.github.mwest2020.jarvis

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.mwest2020.jarvis.core.BridgeClient
import io.github.mwest2020.jarvis.core.BridgeUrl
import io.github.mwest2020.jarvis.core.Built
import io.github.mwest2020.jarvis.core.ListOutcome
import io.github.mwest2020.jarvis.core.ListRequest
import io.github.mwest2020.jarvis.core.Phase
import io.github.mwest2020.jarvis.core.Playback
import io.github.mwest2020.jarvis.core.PlaybackEnd
import io.github.mwest2020.jarvis.core.CancelOutcome
import io.github.mwest2020.jarvis.core.Endpoint
import io.github.mwest2020.jarvis.core.Endpointer
import io.github.mwest2020.jarvis.core.Endpointing
import io.github.mwest2020.jarvis.core.ReadBack
import io.github.mwest2020.jarvis.core.ReadBackCancels
import io.github.mwest2020.jarvis.core.ProjectEntry
import io.github.mwest2020.jarvis.core.ProjectList
import io.github.mwest2020.jarvis.core.ProjectMenu
import io.github.mwest2020.jarvis.core.ProjectPick
import io.github.mwest2020.jarvis.core.ProjectSetting
import io.github.mwest2020.jarvis.core.SilenceAction
import io.github.mwest2020.jarvis.core.SilenceSend
import io.github.mwest2020.jarvis.core.TalkAction
import io.github.mwest2020.jarvis.core.TalkGesture
import io.github.mwest2020.jarvis.core.TurnErrors
import io.github.mwest2020.jarvis.core.TurnOutcome
import io.github.mwest2020.jarvis.core.TurnRequest
import io.github.mwest2020.jarvis.core.TurnRequests
import io.github.mwest2020.jarvis.core.Wav
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.security.GeneralSecurityException
import java.security.ProviderException
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

data class UiState(
    val phase: Phase = Phase.IDLE,
    val projects: List<String> = emptyList(),
    /** The selected project; empty until one is picked (never the first of a longer list by itself; a list of one is selected). */
    val project: String = "",
    /** The "Default project" setting, used when no pick is remembered. */
    val defaultProject: String = "",
    /** The "Home project" setting: what General talks to; blank means no General entry. */
    val homeProject: String = "",
    val summary: String = "",
    val error: String? = null,
    val bridgeUrl: String = "",
    val hasToken: Boolean = false,
    val needsMicrophone: Boolean = false,
    /** The projects field was left empty or `*`: the list comes from the bridge (`GET /projects`). */
    val projectsFromBridge: Boolean = false,
    /** True while `GET /projects` runs. */
    val refreshing: Boolean = false,
    /** False until the stored settings have been read (the Keystore is not asked on the main thread). */
    val loaded: Boolean = false,
    /** The setting "Send when I stop talking" (change jarvis-silence-send, H8). */
    val silenceSend: Boolean = true,
    /** The silence that sends a hands-free recording, 1.5 to 6 s. */
    val silenceSeconds: Double = Endpointing.DEFAULT_SILENCE_SECONDS,
    /** This recording was started with a tap and sends by itself when Mark stops talking (H7). */
    val handsFree: Boolean = false,
    /** Milliseconds until the recording is sent by itself; null while no countdown shows (H6). */
    val countdownMs: Long? = null,
    /** A short sound was set aside during this recording (H4). */
    val shortSound: Boolean = false,
    /** A note that is not an error: a recording was dropped because it held no speech. */
    val notice: String? = null,
) {
    val configured: Boolean get() = bridgeUrl.isNotBlank() && hasToken && (projects.isNotEmpty() || projectsFromBridge)

    /** What the picker shows: General first (for the home project), then the projects. */
    val entries: List<ProjectEntry> get() = ProjectMenu.entries(projects, homeProject)
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = SettingsStore(application)
    private val tokens = TokenStore(application)
    private val route = TailnetRoute(application)
    private val gesture = TalkGesture()
    private val tone = WaitTone()
    private val player = ReplyPlayer()
    private val streamPlayer = StreamPlayer()

    /** One hands-free recording's end-of-speech detection; [posted] is the recorder thread's own. */
    private class Listening(val endpointer: Endpointer) {
        var posted: Endpoint? = null
    }

    // The recording in progress, when it may send by itself (change jarvis-silence-send). Set
    // on the main thread before the recording starts and cleared when it stops; the recorder's
    // thread feeds it.
    @Volatile
    private var listening: Listening? = null
    // Main thread only: the last verdict for the recording in progress.
    private var endpoint: Endpoint? = null
    // Main thread only: whether the reply that plays waits for a yes, and when such a reply last
    // played to its end (the next recording then needs less speech, H5).
    private var replyAsksYes = false
    private var readBackPlayedAt: Long? = null

    private val recorder = Recorder(
        onLimitReached = {
            viewModelScope.launch(Dispatchers.Main) {
                if (state.value.phase == Phase.RECORDING) atLimit()
            }
        },
        onRead = ::heard,
        onFailed = {
            viewModelScope.launch(Dispatchers.Main) {
                if (state.value.phase == Phase.RECORDING) microphoneStopped()
            }
        },
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    // Main thread only. A reply said a new project is ready: fetch the list once the reply has
    // finished playing or was stopped (D56).
    private var refreshAfterReply = false
    // The read-back that is playing, and a cancel that has not reached the bridge yet: a
    // read-back that does not play to its end is cancelled there, before any next turn
    // (jarvis-streaming-speech S6).
    private val readBacks = ReadBackCancels()
    // A refresh was asked for while one was running: one more follows it.
    private var refreshAgain = false
    // The message the last failed list fetch put on screen. A good fetch clears only that one,
    // never an error that came after it (a turn's, a playback's).
    private var listError: String? = null

    init {
        // App start: read the settings, then ask the bridge for its projects if that is the setting.
        viewModelScope.launch {
            reload()
            refreshProjects()
        }
    }

    /** Reads the stored settings off the main thread; the summary on screen is kept. */
    private suspend fun reload() {
        val stored = withContext(Dispatchers.IO) {
            val setting = settings.projectSetting
            val projects = when (setting) {
                ProjectSetting.FromBridge -> ProjectPick.order(settings.fetchedProjects)
                is ProjectSetting.Named -> setting.names
            }
            val default = settings.defaultProject
            val home = settings.homeProject
            UiState(
                projects = projects,
                projectsFromBridge = setting == ProjectSetting.FromBridge,
                project = ProjectPick.choose(projects, settings.pickedProject, default, home),
                defaultProject = default,
                homeProject = home,
                bridgeUrl = settings.bridgeUrl,
                hasToken = tokens.hasToken(),
                loaded = true,
                silenceSend = settings.silenceSend,
                silenceSeconds = settings.silenceSeconds,
            )
        }
        _state.update { stored.copy(summary = it.summary, refreshing = it.refreshing) }
    }

    /**
     * Asks the bridge for its projects (`GET /projects`) when the setting says so: at app
     * start, after saving the settings, and from the refresh button. The same client as a
     * turn: plain http only bound to the Tailscale network, no proxy, no redirects.
     */
    fun refreshProjects() {
        val current = state.value
        if (!current.loaded || !current.projectsFromBridge) return
        if (current.refreshing) {
            refreshAgain = true
            return
        }
        if (current.bridgeUrl.isBlank() || !current.hasToken) return
        _state.update { it.copy(refreshing = true) }
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) {
                // The token is read here, used for this one request, and not kept.
                when (val built = ProjectList.request(current.bridgeUrl, tokens.load())) {
                    is Built.Refused -> ListOutcome.Failed(built.failure)
                    is Built.Ok -> list(built.value)
                }
            }
            when (outcome) {
                is ListOutcome.Failed -> {
                    listError = outcome.failure.message
                    _state.update { it.copy(refreshing = false, error = outcome.failure.message) }
                }
                is ListOutcome.Projects -> {
                    val names = ProjectPick.order(outcome.names)
                    // The remembered pick is only read here, never overwritten: a project that
                    // is missing from one answer is selected again when it is back.
                    val stored = withContext(Dispatchers.IO) {
                        settings.fetchedProjects = names
                        Triple(settings.pickedProject, settings.defaultProject, settings.homeProject)
                    }
                    val empty = if (names.isEmpty()) "The bridge lists no projects." else null
                    _state.update {
                        it.copy(
                            refreshing = false,
                            projects = names,
                            // What is selected stays selected while the list has it (D56); a turn
                            // in flight keeps its project whatever the list says.
                            project = if (it.phase == Phase.IDLE) {
                                ProjectPick.keep(it.project, names, stored.first, stored.second, stored.third)
                            } else {
                                it.project
                            },
                            // Only the list's own earlier error is cleared, never a later one.
                            error = empty ?: if (it.error != null && it.error == listError) null else it.error,
                        )
                    }
                    listError = empty
                }
            }
            if (refreshAgain) {
                refreshAgain = false
                refreshProjects()
            }
        }
    }

    private fun list(request: ListRequest): ListOutcome {
        val client = if (request.cleartext) {
            route.boundClient() ?: return ListOutcome.Failed(TurnErrors.tailscaleOff())
        } else {
            BridgeClient()
        }
        return client.projects(request)
    }

    fun onPress() = act(gesture.onPress(state.value.phase, SystemClock.elapsedRealtime()))

    fun onRelease() {
        act(gesture.onRelease(state.value.phase, SystemClock.elapsedRealtime()))
        // Still recording after the release: it was a tap, so the recording may send by itself.
        if (state.value.phase == Phase.RECORDING && listening != null) {
            _state.update { it.copy(handsFree = true) }
            decide()
        }
    }

    /**
     * The recorder's thread, after each read: the end-of-speech verdict. The main thread hears
     * of it only when it changes; the countdown in tenths of a second is enough for the screen.
     */
    private fun heard(chunk: ByteArray, read: Int) {
        val current = listening ?: return
        val now = current.endpointer.feed(chunk, 0, read)
        val shown = now.copy(countdownMs = now.countdownMs?.let { (it + 99) / 100 * 100 })
        if (shown == current.posted) return
        current.posted = shown
        viewModelScope.launch(Dispatchers.Main) { onEndpoint(current, shown) }
    }

    private fun onEndpoint(from: Listening, now: Endpoint) {
        // A verdict for a recording that has already ended (sent, dropped, tapped) is ignored.
        if (from !== listening || state.value.phase != Phase.RECORDING) return
        endpoint = now
        _state.update { it.copy(countdownMs = now.countdownMs, shortSound = now.shortSound) }
        decide()
    }

    /** What the end of speech means for the recording in progress; nothing while the button is held (H7). */
    private fun decide() {
        val current = state.value
        when (SilenceSend.action(current.silenceSend && listening != null, current.phase, gesture.holding, endpoint)) {
            SilenceAction.SEND -> stopAndSend(toEndOfSpeech = true)
            SilenceAction.DROP -> drop()
            SilenceAction.NONE -> Unit
        }
    }

    /**
     * The two-minute limit: a hands-free recording without speech, or whose "speech" never
     * paused (a room too loud to tell), is dropped; any other is sent (H4).
     */
    private fun atLimit() {
        val heard = listening?.endpointer?.heardSpeech == true
        val paused = listening?.endpointer?.heardPause == true
        when (SilenceSend.atLimit(state.value.handsFree, heard, paused)) {
            SilenceAction.DROP -> drop(SilenceSend.droppedAtLimit(heard, endpoint))
            else -> stopAndSend()
        }
    }

    /** The microphone stopped delivering in the middle of a recording: nothing is sent, the screen says so. */
    private fun microphoneStopped() {
        stopListening()
        recorder.stop()
        fail(MICROPHONE_STOPPED)
    }

    /** A hands-free recording without speech: nothing is sent, a note says so; no error tone (H4). */
    private fun drop(note: String = SilenceSend.dropped(endpoint)) {
        stopListening()
        recorder.stop()
        _state.update { it.copy(phase = Phase.IDLE, notice = note) }
    }

    /** The recording in progress no longer sends by itself. Main thread. */
    private fun stopListening() {
        listening = null
        endpoint = null
        _state.update { it.copy(handsFree = false, countdownMs = null, shortSound = false) }
    }

    private fun act(action: TalkAction) {
        when (action) {
            TalkAction.START_RECORDING -> startRecording()
            TalkAction.STOP_AND_SEND -> stopAndSend()
            TalkAction.STOP_PLAYBACK -> {
                player.stop()
                streamPlayer.stop()
                _state.update { it.copy(phase = Phase.IDLE) }
                playbackEnded(PlaybackEnd.STOPPED)
                afterReply()
            }
            TalkAction.NONE -> Unit
        }
    }

    private fun startRecording() {
        val current = state.value
        // Check the settings before recording, so nobody talks to a client that cannot send.
        val check = TurnRequests.build(current.bridgeUrl, if (current.hasToken) "x" else null, current.project)
        if (check is Built.Refused) {
            fail(check.failure.message)
            return
        }
        val granted = getApplication<Application>()
            .checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            _state.update { it.copy(needsMicrophone = true) }
            return
        }
        // With the setting on, the recording is measured from its first byte, so it can send by
        // itself after a tap; while the button is held it never does (jarvis-silence-send).
        // Only the first recording after a read-back may be a short yes (H5), and never while a
        // read-back cancel is still due; whatever becomes of this recording (sent, dropped, too
        // short), the next one needs the usual speech.
        val now = SystemClock.elapsedRealtime()
        val yes = SilenceSend.yesExpected(readBackPlayedAt, now, readBacks)
        readBackPlayedAt = null
        endpoint = null
        listening = if (current.silenceSend) {
            Listening(Endpointer(Endpointing.forRecording(current.silenceSeconds, yesExpected = yes)))
        } else {
            null
        }
        if (!recorder.start()) {
            listening = null
            fail("The microphone is not available. Is another app using it?")
            return
        }
        _state.update {
            it.copy(phase = Phase.RECORDING, error = null, notice = null, handsFree = false, countdownMs = null, shortSound = false)
        }
    }

    /**
     * Stops the recording and sends it. [toEndOfSpeech]: it ended by itself, and only up to half
     * a second after the last speech goes out (H9). Either way the read-back cancel queue runs
     * first and can refuse the turn.
     */
    private fun stopAndSend(toEndOfSpeech: Boolean = false) {
        val ended = listening
        stopListening()
        val recorded = recorder.stop()
        val pcm = if (toEndOfSpeech && ended != null) recorded.copyOf(ended.endpointer.sendLength(recorded.size)) else recorded
        if (Wav.durationMs(pcm.size) < MIN_RECORDING_MS) {
            _state.update { it.copy(phase = Phase.IDLE, error = "That was too short to send. Hold the button while you talk, or tap to start and tap to stop.") }
            return
        }
        val current = state.value
        _state.update { it.copy(phase = Phase.WAITING, error = null) }
        tone.heard()
        viewModelScope.launch {
            val ticking = launch {
                while (true) {
                    delay(TICK_EVERY_MS)
                    tone.tick()
                }
            }
            // A streamed reply holds an open connection: if this coroutine is cancelled as the
            // reply comes back (the screen closed), it is closed here instead of leaking.
            val handed = AtomicReference<TurnOutcome?>(null)
            val outcome = try {
                withContext(Dispatchers.IO) {
                    // An unheard read-back still pending on the bridge is cancelled first; if
                    // that fails, this turn is not sent (a yes must never confirm it).
                    val refused = readBacks.clearBeforeTurn(SystemClock.elapsedRealtime()) {
                        cancelOnBridge(current, it)
                    }
                    if (refused != null) {
                        TurnOutcome.Failed(refused)
                    } else {
                        // The token is read here, used for this one request, and not kept.
                        when (
                            val built = TurnRequests.build(
                                current.bridgeUrl, tokens.load(), current.project, typedList = !current.projectsFromBridge,
                            )
                        ) {
                            is Built.Refused -> TurnOutcome.Failed(built.failure)
                            is Built.Ok -> send(built.value, Wav.encode(pcm))
                        }
                    }.also { handed.set(it) }
                }
            } catch (cancelled: CancellationException) {
                (handed.get() as? TurnOutcome.Streamed)?.reply?.close()
                throw cancelled
            }
            ticking.cancel()
            when (outcome) {
                is TurnOutcome.Failed -> fail(outcome.failure.message)
                is TurnOutcome.Reply -> {
                    _state.update { it.copy(phase = Phase.PLAYING, summary = outcome.summary, error = null) }
                    // A new project is ready on the agent host (jarvis-general-conversation D56):
                    // the list is fetched again once this reply has played.
                    refreshAfterReply = outcome.projectsChanged
                    startedReadBack(current.project, outcome.needsConfirmation, outcome.confirmationId)
                    player.play(outcome.wav) { played ->
                        _state.update {
                            when {
                                it.phase != Phase.PLAYING -> it
                                played -> it.copy(phase = Phase.IDLE)
                                else -> it.copy(phase = Phase.IDLE, error = "The reply could not be played.")
                            }
                        }
                        playbackEnded(if (played) PlaybackEnd.PLAYED else PlaybackEnd.FAILED)
                        afterReply()
                    }
                }
                is TurnOutcome.Streamed -> {
                    // jarvis-streaming-speech: the headers are in and the audio is coming. The
                    // summary shows now; the first sentence plays as soon as it has arrived.
                    val reply = outcome.reply
                    _state.update { it.copy(phase = Phase.PLAYING, summary = reply.summary, error = null) }
                    refreshAfterReply = reply.projectsChanged
                    startedReadBack(current.project, reply.needsConfirmation, reply.confirmationId)
                    streamPlayer.play(reply) { end ->
                        _state.update {
                            when {
                                it.phase != Phase.PLAYING -> it
                                end == PlaybackEnd.PLAYED -> it.copy(phase = Phase.IDLE)
                                else -> it.copy(phase = Phase.IDLE, error = Playback.message(end))
                            }
                        }
                        playbackEnded(end)
                        afterReply()
                    }
                }
            }
        }
    }

    private fun startedReadBack(project: String, needsConfirmation: Boolean, id: String?) {
        replyAsksYes = needsConfirmation
        if (needsConfirmation && !id.isNullOrEmpty()) {
            readBacks.started(project, id, SystemClock.elapsedRealtime())
        } else {
            readBacks.played()
        }
    }

    /**
     * A reply has ended. A read-back that did not play to its end was not heard: it is queued
     * and the bridge is told to cancel it (`POST /turn/cancel`); the screen says so. When that
     * fails the cancel stays queued and goes out again before the next turn. Main thread.
     */
    private fun playbackEnded(end: PlaybackEnd) {
        val asksYes = replyAsksYes
        replyAsksYes = false
        if (end == PlaybackEnd.PLAYED) {
            readBacks.played()
            // A read-back was heard to its end: the next recording may be a short yes (H5).
            readBackPlayedAt = if (asksYes) SystemClock.elapsedRealtime() else null
            return
        }
        readBackPlayedAt = null
        val item = readBacks.notPlayed(SystemClock.elapsedRealtime()) ?: return
        val settings = state.value
        // No new turn until the bridge has been told: a yes must never overtake the cancel.
        _state.update { it.copy(phase = Phase.WAITING) }
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) { cancelOnBridge(settings, item) }
            val message = if (outcome is CancelOutcome.Done) {
                readBacks.sent(item)
                ReadBack.CANCELLED
            } else {
                ReadBack.NOT_CANCELLED
            }
            _state.update { it.copy(phase = Phase.IDLE, error = message) }
        }
    }

    /** Blocking: the cancel request for one read-back, with the same route rules as a turn. */
    private fun cancelOnBridge(settings: UiState, item: ReadBackCancels.Item): CancelOutcome =
        when (val built = TurnRequests.cancel(settings.bridgeUrl, tokens.load(), item.project, item.id)) {
            is Built.Refused -> CancelOutcome.Failed(built.failure)
            is Built.Ok -> {
                val request = built.value
                val client = if (request.cleartext) route.boundClient() else BridgeClient()
                client?.cancel(request) ?: CancelOutcome.Failed(TurnErrors.tailscaleOff())
            }
        }

    /** The reply has finished playing, or was stopped: the refresh it asked for, if any. */
    private fun afterReply() {
        if (!refreshAfterReply) return
        refreshAfterReply = false
        refreshProjects()
    }

    /**
     * Plain http goes out bound to the Tailscale VPN network, or not at all. https is
     * authenticated by its certificate and uses the default network; neither uses a proxy.
     */
    private fun send(request: TurnRequest, wav: ByteArray): TurnOutcome {
        val client = if (request.cleartext) {
            route.boundClient() ?: return TurnOutcome.Failed(TurnErrors.tailscaleOff())
        } else {
            BridgeClient()
        }
        return client.turn(request, wav)
    }

    private fun fail(message: String) {
        tone.problem()
        _state.update { it.copy(phase = Phase.IDLE, error = message) }
    }

    fun onMicrophoneAnswer(granted: Boolean) {
        _state.update {
            it.copy(
                needsMicrophone = false,
                error = if (granted) null else "Jarvis needs the microphone to hear the question. Allow it in the phone's app settings.",
            )
        }
    }

    fun selectProject(name: String) {
        if (state.value.phase != Phase.IDLE) return
        settings.pickedProject = name
        _state.update { it.copy(project = name, summary = "", error = null, notice = null) }
    }

    /** The setting "Send when I stop talking" and its silence; stored at once, no Save needed (H8). */
    fun setSilenceSend(enabled: Boolean, seconds: Double) {
        val clamped = Endpointing.silenceSeconds(seconds)
        settings.silenceSend = enabled
        settings.silenceSeconds = clamped
        _state.update { it.copy(silenceSend = enabled, silenceSeconds = clamped) }
    }

    /**
     * Saves the settings and reports null, or the reason they were refused. An empty
     * [newToken] keeps the stored one. The Keystore and the files are used off the main
     * thread; [onResult] runs on it.
     */
    fun saveSettings(
        bridgeUrl: String,
        newToken: String,
        projectsText: String,
        defaultProject: String,
        homeProject: String,
        onResult: (String?) -> Unit,
    ) {
        val url = when (val checked = BridgeUrl.validate(bridgeUrl)) {
            is Built.Refused -> return onResult(checked.failure.message)
            is Built.Ok -> checked.value.toString()
        }
        val token = newToken.trim()
        if (token.isNotEmpty() && !TurnRequests.isValidToken(token)) {
            return onResult("The token must not contain spaces or unusual characters.")
        }
        val setting = ProjectSetting.parse(projectsText)
        viewModelScope.launch {
            val refused = withContext(Dispatchers.IO) {
                try {
                    if (token.isNotEmpty()) tokens.save(token)
                    settings.bridgeUrl = url
                    settings.projectSetting = setting
                    settings.defaultProject = defaultProject
                    settings.homeProject = homeProject
                    null
                } catch (_: GeneralSecurityException) {
                    KEYSTORE_REFUSED
                } catch (_: ProviderException) {
                    KEYSTORE_REFUSED
                }
            }
            if (refused == null) reload()
            onResult(refused)
            if (refused == null) refreshProjects()
        }
    }

    fun forgetToken() {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { tokens.clear() }
            _state.update { it.copy(hasToken = false) }
        }
    }

    /** The app left the screen: a recording in progress is dropped, never sent later. */
    fun onBackground() {
        if (state.value.phase == Phase.RECORDING) {
            stopListening()
            recorder.stop()
            _state.update { it.copy(phase = Phase.IDLE) }
        }
    }

    override fun onCleared() {
        listening = null
        recorder.stop()
        player.stop()
        streamPlayer.stop()
        // The screen is gone while a read-back plays: cancel it on a thread of its own, the
        // view model's scope is already cancelled. If the process dies first, the agent host
        // drops the call when it expires (at most a minute).
        readBacks.outstanding(SystemClock.elapsedRealtime())?.let { item ->
            val settings = state.value
            thread(name = "jarvis-cancel-read-back") {
                try {
                    cancelOnBridge(settings, item)
                } catch (_: RuntimeException) {
                    // Best effort; the call expires by itself.
                }
            }
        }
        tone.release()
    }

    private companion object {
        const val MIN_RECORDING_MS = 300L
        const val TICK_EVERY_MS = 3_000L
        const val KEYSTORE_REFUSED = "The phone's key store refused to store the token."
        const val MICROPHONE_STOPPED = "The microphone stopped, so nothing was sent. Tap to try again."
    }
}
