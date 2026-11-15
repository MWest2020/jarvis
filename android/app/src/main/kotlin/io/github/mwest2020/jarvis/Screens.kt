package io.github.mwest2020.jarvis

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.mwest2020.jarvis.core.Phase
import io.github.mwest2020.jarvis.core.ProjectSetting

private val Background = Color(0xFF0B0F14)
private val Panel = Color(0xFF16202B)
private val Accent = Color(0xFF4FA3E0)
private val Recording = Color(0xFFD9483B)
private val Working = Color(0xFF8A6D1F)
private val Speaking = Color(0xFF2E8B57)
private val ErrorText = Color(0xFFFF8A80)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Accent,
            onPrimary = Color.Black,
            background = Background,
            surface = Background,
            onBackground = Color.White,
            onSurface = Color.White,
            error = ErrorText,
        ),
    ) {
        Surface(modifier = Modifier.fillMaxSize(), color = Background, content = content)
    }
}

/** One screen: the projects, one large button, the summary of the last reply. */
@Composable
fun TalkScreen(
    state: UiState,
    onPress: () -> Unit,
    onRelease: () -> Unit,
    onSelectProject: (String) -> Unit,
    onOpenSettings: () -> Unit,
    onRefreshProjects: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Jarvis", fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            if (state.projectsFromBridge) {
                // The list comes from the bridge (GET /projects): fetched at start, and here.
                TextButton(onClick = onRefreshProjects, enabled = state.phase == Phase.IDLE && !state.refreshing) {
                    Text(if (state.refreshing) "Refreshing" else "Refresh", fontSize = 18.sp)
                }
            }
            TextButton(onClick = onOpenSettings, enabled = state.phase == Phase.IDLE) {
                Text("Settings", fontSize = 18.sp)
            }
        }

        if (state.projects.isEmpty() && state.projectsFromBridge) {
            Text(
                if (state.refreshing) "Asking the bridge for its projects…" else "No projects yet. Tap Refresh.",
                color = Color(0xFF9FB0C0),
                fontSize = 18.sp,
            )
        }

        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(state.projects) { name ->
                val padding = PaddingValues(horizontal = 20.dp, vertical = 14.dp)
                if (name == state.project) {
                    Button(onClick = {}, contentPadding = padding) { Text(name, fontSize = 18.sp) }
                } else {
                    OutlinedButton(
                        onClick = { onSelectProject(name) },
                        enabled = state.phase == Phase.IDLE,
                        contentPadding = padding,
                    ) { Text(name, fontSize = 18.sp) }
                }
            }
        }

        TalkButton(state.phase, onPress, onRelease, Modifier.weight(1f))

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 96.dp, max = 220.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(Panel)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            val error = state.error
            when {
                error != null -> Text(error, color = ErrorText, fontSize = 20.sp, lineHeight = 26.sp)
                state.summary.isNotEmpty() -> Text(state.summary, fontSize = 20.sp, lineHeight = 26.sp)
                else -> Text(
                    "Hold the button while you talk, or tap to start and tap again to send.",
                    color = Color(0xFF9FB0C0),
                    fontSize = 18.sp,
                    lineHeight = 24.sp,
                )
            }
        }
    }
}

@Composable
private fun TalkButton(phase: Phase, onPress: () -> Unit, onRelease: () -> Unit, modifier: Modifier = Modifier) {
    val press by rememberUpdatedState(onPress)
    val release by rememberUpdatedState(onRelease)
    val (color, label, hint) = when (phase) {
        Phase.IDLE -> Triple(Accent, "Talk", "hold, or tap")
        Phase.RECORDING -> Triple(Recording, "Listening", "let go or tap to send")
        Phase.WAITING -> Triple(Working, "Working", "the reply takes about 20 seconds")
        Phase.PLAYING -> Triple(Speaking, "Speaking", "tap to stop")
    }
    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(32.dp))
            .background(color)
            .semantics {
                role = Role.Button
                contentDescription = "$label, $hint"
            }
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown()
                    press()
                    waitForUpOrCancellation()
                    release()
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, fontSize = 44.sp, fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(Modifier.height(8.dp))
            Text(hint, fontSize = 18.sp, color = Color.White, textAlign = TextAlign.Center)
        }
    }
}

/**
 * The bridge address, the token and the project names. The stored token is never shown or
 * put back into the field; an empty field keeps it.
 */
@Composable
fun SettingsScreen(
    state: UiState,
    onSave: (url: String, token: String, projects: String, done: (String?) -> Unit) -> Unit,
    onForgetToken: () -> Unit,
    onClose: (() -> Unit)?,
) {
    var url by rememberSaveable { mutableStateOf(state.bridgeUrl) }
    // Not saveable on purpose: a typed token is not written into the saved instance state.
    var token by remember { mutableStateOf("") }
    var projects by rememberSaveable {
        mutableStateOf(if (state.projectsFromBridge) ProjectSetting.FROM_BRIDGE else state.projects.joinToString(", "))
    }
    var error by rememberSaveable { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Settings", fontSize = 22.sp, fontWeight = FontWeight.Bold)

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Bridge address") },
            placeholder = { Text("http://jarvis.your-tailnet.ts.net:8787") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Plain http is only accepted for a Tailscale name (*.ts.net), where WireGuard " +
                "encrypts the connection. Anything else needs https.",
            color = Color(0xFF9FB0C0),
        )

        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text(if (state.hasToken) "Token (stored; leave empty to keep it)" else "Token") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        if (state.hasToken) {
            TextButton(onClick = onForgetToken) { Text("Forget the stored token") }
        }

        OutlinedTextField(
            value = projects,
            onValueChange = { projects = it },
            label = { Text("Projects (names on the bridge, comma-separated)") },
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Leave it empty or type * to get the list from the bridge; it is fetched when the " +
                "app starts and with Refresh on the main screen.",
            color = Color(0xFF9FB0C0),
        )

        error?.let { Text(it, color = ErrorText, fontSize = 18.sp) }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = {
                    onSave(url, token, projects) { refused ->
                        error = refused
                        if (refused == null) token = ""
                    }
                },
                contentPadding = PaddingValues(horizontal = 28.dp, vertical = 14.dp),
            ) { Text("Save", fontSize = 18.sp) }
            if (onClose != null) {
                OutlinedButton(
                    onClick = onClose,
                    contentPadding = PaddingValues(horizontal = 28.dp, vertical = 14.dp),
                ) { Text("Back", fontSize = 18.sp) }
            }
        }
    }
}
