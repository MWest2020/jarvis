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
import io.github.mwest2020.jarvis.core.Phase
import io.github.mwest2020.jarvis.core.TalkAction
import io.github.mwest2020.jarvis.core.TalkGesture
import io.github.mwest2020.jarvis.core.TurnOutcome
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
) {
    val configured: Boolean get() = bridgeUrl.isNotBlank() && hasToken && projects.isNotEmpty()
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = SettingsStore(application)
    private val tokens = TokenStore(application)
    private val client = BridgeClient()
    private val gesture = TalkGesture()
    private val tone = WaitTone()
    private val player = ReplyPlayer(application.cacheDir)
    private val recorder = Recorder(onLimitReached = {
        viewModelScope.launch(Dispatchers.Main) {
            if (state.value.phase == Phase.RECORDING) stopAndSend()
        }
    })

    private val _state = MutableStateFlow(loadState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private fun loadState(): UiState {
        val projects = settings.projects
        val project = settings.project.takeIf { it in projects } ?: projects.firstOrNull().orEmpty()
        return UiState(
            projects = projects,
            project = project,
            bridgeUrl = settings.bridgeUrl,
            hasToken = tokens.hasToken(),
        )
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
                    is Built.Ok -> client.turn(built.value, Wav.encode(pcm))
                }
            }
            ticking.cancel()
            when (outcome) {
                is TurnOutcome.Failed -> fail(outcome.failure.message)
                is TurnOutcome.Reply -> {
                    _state.update { it.copy(phase = Phase.PLAYING, summary = outcome.summary, error = null) }
                    val started = player.play(outcome.wav) {
                        _state.update { if (it.phase == Phase.PLAYING) it.copy(phase = Phase.IDLE) else it }
                    }
                    if (!started) {
                        _state.update { it.copy(phase = Phase.IDLE, error = "The reply could not be played. The summary is above.") }
                    }
                }
            }
        }
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
     * Saves the settings, or returns the reason they were refused. An empty [newToken] keeps
     * the stored one.
     */
    fun saveSettings(bridgeUrl: String, newToken: String, projectsText: String): String? {
        val url = when (val checked = BridgeUrl.validate(bridgeUrl)) {
            is Built.Refused -> return checked.failure.message
            is Built.Ok -> checked.value.toString()
        }
        val token = newToken.trim()
        if (token.isNotEmpty() && !TurnRequests.isValidToken(token)) {
            return "The token must not contain spaces or unusual characters."
        }
        val projects = SettingsStore.parseProjects(projectsText)
        if (projects.isEmpty()) return "Add at least one project name."
        if (token.isNotEmpty()) {
            try {
                tokens.save(token)
            } catch (_: GeneralSecurityException) {
                return KEYSTORE_REFUSED
            } catch (_: ProviderException) {
                return KEYSTORE_REFUSED
            }
        }
        settings.bridgeUrl = url
        settings.projects = projects
        _state.update { loadState().copy(summary = it.summary) }
        settings.project = state.value.project
        return null
    }

    fun forgetToken() {
        tokens.clear()
        _state.update { it.copy(hasToken = false) }
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
