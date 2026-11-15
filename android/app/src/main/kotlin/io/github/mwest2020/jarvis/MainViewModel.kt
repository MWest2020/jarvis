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
import io.github.mwest2020.jarvis.core.ProjectList
import io.github.mwest2020.jarvis.core.ProjectSetting
import io.github.mwest2020.jarvis.core.TalkAction
import io.github.mwest2020.jarvis.core.TalkGesture
import io.github.mwest2020.jarvis.core.TurnErrors
import io.github.mwest2020.jarvis.core.TurnOutcome
import io.github.mwest2020.jarvis.core.TurnRequest
import io.github.mwest2020.jarvis.core.TurnRequests
import io.github.mwest2020.jarvis.core.Wav
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

data class UiState(
    val phase: Phase = Phase.IDLE,
    val projects: List<String> = emptyList(),
    val project: String = "",
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
) {
    val configured: Boolean get() = bridgeUrl.isNotBlank() && hasToken && (projects.isNotEmpty() || projectsFromBridge)
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = SettingsStore(application)
    private val tokens = TokenStore(application)
    private val route = TailnetRoute(application)
    private val gesture = TalkGesture()
    private val tone = WaitTone()
    private val player = ReplyPlayer()
    private val recorder = Recorder(onLimitReached = {
        viewModelScope.launch(Dispatchers.Main) {
            if (state.value.phase == Phase.RECORDING) stopAndSend()
        }
    })

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

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
                ProjectSetting.FromBridge -> settings.fetchedProjects
                is ProjectSetting.Named -> setting.names
            }
            UiState(
                projects = projects,
                projectsFromBridge = setting == ProjectSetting.FromBridge,
                project = settings.project.takeIf { it in projects } ?: projects.firstOrNull().orEmpty(),
                bridgeUrl = settings.bridgeUrl,
                hasToken = tokens.hasToken(),
                loaded = true,
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
        if (!current.loaded || !current.projectsFromBridge || current.refreshing) return
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
                is ListOutcome.Failed -> _state.update { it.copy(refreshing = false, error = outcome.failure.message) }
                is ListOutcome.Projects -> {
                    val names = outcome.names
                    val pick = state.value.project.takeIf { it in names } ?: names.firstOrNull().orEmpty()
                    withContext(Dispatchers.IO) {
                        settings.fetchedProjects = names
                        settings.project = pick
                    }
                    _state.update {
                        it.copy(
                            refreshing = false,
                            projects = names,
                            // A turn in flight keeps its project; the pick changes only when idle.
                            project = if (it.phase == Phase.IDLE) pick else it.project,
                            error = if (names.isEmpty()) "The bridge lists no projects." else null,
                        )
                    }
                }
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

    fun onRelease() = act(gesture.onRelease(state.value.phase, SystemClock.elapsedRealtime()))

    private fun act(action: TalkAction) {
        when (action) {
            TalkAction.START_RECORDING -> startRecording()
            TalkAction.STOP_AND_SEND -> stopAndSend()
            TalkAction.STOP_PLAYBACK -> {
                player.stop()
                _state.update { it.copy(phase = Phase.IDLE) }
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
        if (!recorder.start()) {
            fail("The microphone is not available. Is another app using it?")
            return
        }
        _state.update { it.copy(phase = Phase.RECORDING, error = null) }
    }

    private fun stopAndSend() {
        val pcm = recorder.stop()
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
            val outcome = withContext(Dispatchers.IO) {
                // The token is read here, used for this one request, and not kept.
                when (val built = TurnRequests.build(current.bridgeUrl, tokens.load(), current.project)) {
                    is Built.Refused -> TurnOutcome.Failed(built.failure)
                    is Built.Ok -> send(built.value, Wav.encode(pcm))
                }
            }
            ticking.cancel()
            when (outcome) {
                is TurnOutcome.Failed -> fail(outcome.failure.message)
                is TurnOutcome.Reply -> {
                    _state.update { it.copy(phase = Phase.PLAYING, summary = outcome.summary, error = null) }
                    player.play(outcome.wav) { played ->
                        _state.update {
                            when {
                                it.phase != Phase.PLAYING -> it
                                played -> it.copy(phase = Phase.IDLE)
                                else -> it.copy(phase = Phase.IDLE, error = "The reply could not be played.")
                            }
                        }
                    }
                }
            }
        }
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
        settings.project = name
        _state.update { it.copy(project = name, summary = "", error = null) }
    }

    /**
     * Saves the settings and reports null, or the reason they were refused. An empty
     * [newToken] keeps the stored one. The Keystore and the files are used off the main
     * thread; [onResult] runs on it.
     */
    fun saveSettings(bridgeUrl: String, newToken: String, projectsText: String, onResult: (String?) -> Unit) {
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
                    if (setting is ProjectSetting.Named && settings.project !in setting.names) {
                        settings.project = setting.names.first()
                    }
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
            recorder.stop()
            _state.update { it.copy(phase = Phase.IDLE) }
        }
    }

    override fun onCleared() {
        recorder.stop()
        player.stop()
        tone.release()
    }

    private companion object {
        const val MIN_RECORDING_MS = 300L
        const val TICK_EVERY_MS = 3_000L
        const val KEYSTORE_REFUSED = "The phone's key store refused to store the token."
    }
}
