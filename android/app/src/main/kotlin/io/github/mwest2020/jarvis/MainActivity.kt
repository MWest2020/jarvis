package io.github.mwest2020.jarvis

import android.Manifest
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // In a mount in a parked car the screen should not go dark in the middle of a turn.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            val state by viewModel.state.collectAsState()
            var showSettings by rememberSaveable { mutableStateOf(false) }
            val askMicrophone = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission(),
                viewModel::onMicrophoneAnswer,
            )
            LaunchedEffect(state.needsMicrophone) {
                if (state.needsMicrophone) askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
            }
            JarvisTheme {
                if (!state.loaded) {
                    // The stored settings are being read; a moment of the dark background.
                } else if (showSettings || !state.configured) {
                    BackHandler(enabled = showSettings && state.configured) { showSettings = false }
                    SettingsScreen(
                        state = state,
                        onSave = { url, token, projects, done ->
                            viewModel.saveSettings(url, token, projects) { error ->
                                if (error == null) showSettings = false
                                done(error)
                            }
                        },
                        onForgetToken = viewModel::forgetToken,
                        onClose = if (state.configured) ({ showSettings = false }) else null,
                    )
                } else {
                    TalkScreen(
                        state = state,
                        onPress = viewModel::onPress,
                        onRelease = viewModel::onRelease,
                        onSelectProject = viewModel::selectProject,
                        onOpenSettings = { showSettings = true },
                    )
                }
            }
        }
    }

    override fun onStop() {
        super.onStop()
        viewModel.onBackground()
    }
}
