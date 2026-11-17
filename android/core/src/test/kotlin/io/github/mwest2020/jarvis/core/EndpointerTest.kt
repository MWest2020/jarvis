package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.Random
import kotlin.math.PI
import kotlin.math.sin

/** End of speech from synthetic PCM (change jarvis-silence-send, H1 to H9). */
class EndpointerTest {
    /** Builds 16 kHz mono 16-bit little-endian PCM: background noise, with or without a "voice" on top. */
    private class Pcm(private val noise: Double = 100.0, seed: Long = 7) {
        private val random = Random(seed)
        private val out = ByteArrayOutputStream()
        private var n = 0L

        fun silence(ms: Long) = add(ms) { 0.0 }

        /** A 220 Hz tone with a slow wobble in loudness, like syllables. */
        fun speech(ms: Long, amplitude: Double = 4_000.0) = add(ms) { t ->
            amplitude * (0.75 + 0.25 * sin(2 * PI * 4 * t)) * sin(2 * PI * 220 * t)
        }

        /** Full-scale square wave: clipped, the loudest a sample gets. */
        fun clipped(ms: Long) = add(ms) { t -> if (sin(2 * PI * 220 * t) >= 0) 40_000.0 else -40_000.0 }

        private fun add(ms: Long, voice: (Double) -> Double): Pcm {
            repeat((ms * Wav.SAMPLE_RATE / 1_000).toInt()) {
                val t = n++.toDouble() / Wav.SAMPLE_RATE
                val v = (voice(t) + random.nextGaussian() * noise).toInt().coerceIn(-32_768, 32_767)
                out.write(v and 0xFF)
                out.write((v shr 8) and 0xFF)
            }
            return this
        }

        fun bytes(): ByteArray = out.toByteArray()
    }

    /** One step of the run: how far into the recording, and what the endpointer said there. */
    private data class Step(val ms: Long, val endpoint: Endpoint)

    private fun run(endpointer: Endpointer, pcm: ByteArray, chunk: Int = 640): List<Step> {
        val steps = ArrayList<Step>()
        var at = 0
        while (at < pcm.size) {
            val n = minOf(chunk, pcm.size - at)
            val endpoint = endpointer.feed(pcm, at, n)
            at += n
            steps += Step(at * 1_000L / Wav.BYTES_PER_SECOND, endpoint)
        }
        return steps
    }

    private fun List<Step>.first(event: EndpointEvent): Long? = firstOrNull { it.endpoint.event == event }?.ms

    private fun List<Step>.at(ms: Long): Endpoint = first { it.ms >= ms }.endpoint

    @Test
    fun aQuestionThenSilenceSendsAfterTheSilence() {
        val pcm = Pcm().silence(300).speech(1_500).silence(4_000).bytes()
        val endpointer = Endpointer()
        val steps = run(endpointer, pcm)
        // Speech ends at 1.8 s; three seconds of silence later it sends.
        assertEquals(4_800L, steps.first(EndpointEvent.SEND))
        assertTrue(endpointer.heardSpeech)
        // Up to the tail after the last speech: 1.8 s + 0.5 s.
        assertEquals(2_300 * Wav.BYTES_PER_SECOND / 1_000, endpointer.sendLength(pcm.size))
        // Once it has said SEND it keeps saying so.
        assertEquals(EndpointEvent.SEND, steps.last().endpoint.event)
    }

    @Test
    fun theCountdownShowsAfterHalfASecondOfSilence() {
        val steps = run(Endpointer(), Pcm().silence(300).speech(1_500).silence(4_000).bytes())
        assertNull(steps.at(1_700).countdownMs)
        assertNull(steps.at(2_200).countdownMs)
        assertEquals(2_000L, steps.at(2_800).countdownMs)
        assertEquals(20L, steps.at(4_780).countdownMs)
        assertNull(steps.at(4_800).countdownMs)
    }

    @Test
    fun aPauseShorterThanTheSilenceDoesNotSend() {
        val pcm = Pcm().silence(300).speech(1_000).silence(2_500).speech(1_000).silence(3_200).bytes()
        val steps = run(Endpointer(), pcm)
        // The pause counts down and stops counting when speech comes back.
        assertNotNull(steps.at(2_500).countdownMs)
        assertNull(steps.at(4_000).countdownMs)
        assertEquals(EndpointEvent.LISTENING, steps.at(4_000).event)
        assertEquals(7_800L, steps.first(EndpointEvent.SEND))
    }

    @Test
    fun theSilenceSettingIsUsed() {
        val pcm = Pcm().silence(300).speech(1_000).silence(2_000).bytes()
        assertEquals(2_800L, run(Endpointer(Endpointing.forRecording(1.5, yesExpected = false)), pcm).first(EndpointEvent.SEND))
        assertNull(run(Endpointer(), pcm).first(EndpointEvent.SEND))
    }

    @Test
    fun theNoiseFloorFollowsALoudRoom() {
        // A car: noise at RMS 800, more than the first floor may be; speech well above it.
        val endpointer = Endpointer()
        val steps = run(endpointer, Pcm(noise = 800.0).silence(3_000).speech(1_500, amplitude = 9_000.0).silence(3_500).bytes())
        assertEquals(EndpointEvent.LISTENING, steps.at(3_000).event)
        assertEquals(800.0, endpointer.noiseFloor, 100.0)
        assertEquals(7_500L, steps.first(EndpointEvent.SEND))
    }

    @Test
    fun noiseAloneIsNeverSent() {
        val endpointer = Endpointer()
        val steps = run(endpointer, Pcm(noise = 800.0).silence(12_000).bytes())
        assertNull(steps.first(EndpointEvent.SEND))
        assertEquals(10_000L, steps.first(EndpointEvent.NOTHING_HEARD))
        assertFalse(steps.last().endpoint.shortSound)
        assertFalse(endpointer.heardSpeech)
    }

    @Test
    fun aMutedMicrophoneDoesNotMakeABreathSpeech() {
        // Digital silence at the start, then faint noise: the floor stays at its minimum.
        val pcm = Pcm(noise = 0.0).silence(500).bytes() + Pcm(noise = 100.0).silence(11_000).bytes()
        val endpointer = Endpointer()
        val steps = run(endpointer, pcm)
        assertEquals(50.0, Endpointer().also { run(it, Pcm(noise = 0.0).silence(500).bytes()) }.noiseFloor, 0.001)
        assertNull(steps.first(EndpointEvent.SEND))
        assertEquals(EndpointEvent.NOTHING_HEARD, steps.last().endpoint.event)
    }

    @Test
    fun aCoughIsSetAsideAndNeverSent() {
        val steps = run(Endpointer(), Pcm().silence(300).speech(300, amplitude = 8_000.0).silence(11_000).bytes())
        assertNull(steps.first(EndpointEvent.SEND))
        assertFalse(steps.at(3_500).shortSound)
        // Three seconds after the cough it is set aside, and the app keeps listening.
        assertTrue(steps.at(3_600).shortSound)
        assertEquals(EndpointEvent.LISTENING, steps.at(3_600).event)
        // Ten seconds after the last sound the recording is dropped.
        assertEquals(10_600L, steps.first(EndpointEvent.NOTHING_HEARD))
        assertTrue(steps.last().endpoint.shortSound)
        assertEquals(SilenceSend.SHORT_SOUND_DROPPED, SilenceSend.dropped(steps.last().endpoint))
    }

    @Test
    fun aSetAsideSoundDoesNotAddToLaterSpeech() {
        // 0.4 s, a silence, 0.4 s: 0.8 s together, but never 0.6 s in one utterance.
        val pcm = Pcm().silence(300).speech(400).silence(3_200).speech(400).silence(3_500).bytes()
        assertNull(run(Endpointer(), pcm).first(EndpointEvent.SEND))
    }

    @Test
    fun aQuestionAfterACoughIsSent() {
        val pcm = Pcm().silence(300).speech(300).silence(3_500).speech(1_200).silence(3_200).bytes()
        val steps = run(Endpointer(), pcm)
        assertTrue(steps.at(4_000).shortSound)
        assertEquals(8_300L, steps.first(EndpointEvent.SEND))
        assertFalse(steps.last().endpoint.shortSound)
    }

    @Test
    fun longSilenceFirstIsNotCutOff() {
        val pcm = Pcm().silence(9_000).speech(1_000).silence(3_200).bytes()
        val steps = run(Endpointer(), pcm)
        assertNull(steps.first(EndpointEvent.NOTHING_HEARD))
        assertEquals(13_000L, steps.first(EndpointEvent.SEND))
    }

    @Test
    fun clippedSpeechCountsAndDoesNotOverflow() {
        val endpointer = Endpointer()
        val steps = run(endpointer, Pcm().silence(300).clipped(1_000).silence(3_200).bytes())
        assertEquals(4_300L, steps.first(EndpointEvent.SEND))
        assertTrue(endpointer.noiseFloor < 200)
    }

    @Test
    fun speechFromTheFirstFrameCounts() {
        // No quiet start to measure: the first floor is capped, and the start is classified after.
        val endpointer = Endpointer()
        val steps = run(endpointer, Pcm().speech(1_000, amplitude = 6_000.0).silence(3_200).bytes())
        assertEquals(4_000L, steps.first(EndpointEvent.SEND))
        assertEquals(1_500 * Wav.BYTES_PER_SECOND / 1_000, endpointer.sendLength(Int.MAX_VALUE))
    }

    @Test
    fun clippedSpeechFromTheFirstFrameCounts() {
        val steps = run(Endpointer(), Pcm().clipped(800).silence(3_200).bytes())
        assertEquals(3_800L, steps.first(EndpointEvent.SEND))
    }

    @Test
    fun readsOfAnySizeGiveTheSameAnswer() {
        val pcm = Pcm().silence(300).speech(1_100).silence(3_100).bytes()
        val whole = Endpointer()
        val expected = whole.feed(pcm)
        assertEquals(EndpointEvent.SEND, expected.event)
        for (chunk in listOf(1, 3, 7, 641, 3_841)) {
            val split = Endpointer()
            val last = run(split, pcm, chunk).last().endpoint
            assertEquals("chunk $chunk", expected, last)
            assertEquals("chunk $chunk", whole.sendLength(pcm.size), split.sendLength(pcm.size))
        }
    }

    @Test
    fun aSpokenYesNeedsTheShorterMinimum() {
        val pcm = Pcm().silence(300).speech(360).silence(3_200).bytes()
        assertNull(run(Endpointer(Endpointing.forRecording(3.0, yesExpected = false)), pcm).first(EndpointEvent.SEND))
        assertEquals(3_660L, run(Endpointer(Endpointing.forRecording(3.0, yesExpected = true)), pcm).first(EndpointEvent.SEND))
    }

    @Test
    fun nothingBeforeTheFloorIsKnown() {
        val endpointer = Endpointer()
        assertEquals(Endpoint(), endpointer.feed(Pcm().speech(200).bytes()))
        assertEquals(Endpoint(), endpointer.feed(ByteArray(0)))
    }

    @Test
    fun theSilenceSettingIsClampedToHalfSeconds() {
        assertEquals(3.0, Endpointing.silenceSeconds(3.0), 0.0)
        assertEquals(1.5, Endpointing.silenceSeconds(1.0), 0.0)
        assertEquals(6.0, Endpointing.silenceSeconds(9.0), 0.0)
        assertEquals(2.5, Endpointing.silenceSeconds(2.3), 0.0)
        assertEquals(2.0, Endpointing.silenceSeconds(2.2), 0.0)
        assertEquals(3.0, Endpointing.silenceSeconds(Double.NaN), 0.0)
        assertEquals(6.0, Endpointing.silenceSeconds(Double.POSITIVE_INFINITY), 0.0)
        assertEquals("3 s", Endpointing.label(3.0))
        assertEquals("1.5 s", Endpointing.label(1.5))
        assertEquals(4_500L, Endpointing.forRecording(4.5, yesExpected = false).silenceMs)
        assertEquals(Endpointing.MIN_SPEECH_MS, Endpointing.forRecording(4.5, yesExpected = false).minSpeechMs)
        assertEquals(Endpointing.MIN_YES_MS, Endpointing.forRecording(4.5, yesExpected = true).minSpeechMs)
        assertEquals(Endpointing(), Endpointing.forRecording(Endpointing.DEFAULT_SILENCE_SECONDS, yesExpected = false))
    }
}

/** What the app does with a verdict (H4, H5, H7). */
class SilenceSendTest {
    private val send = Endpoint(EndpointEvent.SEND)
    private val nothing = Endpoint(EndpointEvent.NOTHING_HEARD)

    @Test
    fun aTappedRecordingSendsAndDrops() {
        assertEquals(SilenceAction.SEND, SilenceSend.action(true, Phase.RECORDING, holding = false, endpoint = send))
        assertEquals(SilenceAction.DROP, SilenceSend.action(true, Phase.RECORDING, holding = false, endpoint = nothing))
        assertEquals(SilenceAction.NONE, SilenceSend.action(true, Phase.RECORDING, holding = false, endpoint = Endpoint()))
        assertEquals(SilenceAction.NONE, SilenceSend.action(true, Phase.RECORDING, holding = false, endpoint = null))
    }

    @Test
    fun nothingWhileHeldOffOrNotRecording() {
        assertEquals(SilenceAction.NONE, SilenceSend.action(true, Phase.RECORDING, holding = true, endpoint = send))
        assertEquals(SilenceAction.NONE, SilenceSend.action(true, Phase.RECORDING, holding = true, endpoint = nothing))
        assertEquals(SilenceAction.NONE, SilenceSend.action(false, Phase.RECORDING, holding = false, endpoint = send))
        for (phase in listOf(Phase.IDLE, Phase.WAITING, Phase.PLAYING)) {
            assertEquals(SilenceAction.NONE, SilenceSend.action(true, phase, holding = false, endpoint = send))
        }
    }

    @Test
    fun theLimitDropsAHandsFreeRecordingWithoutSpeech() {
        assertEquals(SilenceAction.DROP, SilenceSend.atLimit(handsFree = true, heardSpeech = false))
        assertEquals(SilenceAction.SEND, SilenceSend.atLimit(handsFree = true, heardSpeech = true))
        // Held, or with the setting off, the limit sends as before.
        assertEquals(SilenceAction.SEND, SilenceSend.atLimit(handsFree = false, heardSpeech = false))
    }

    @Test
    fun theNotes() {
        assertEquals(SilenceSend.NOTHING_HEARD, SilenceSend.dropped(nothing))
        assertEquals(SilenceSend.NOTHING_HEARD, SilenceSend.dropped(null))
        assertEquals(SilenceSend.SHORT_SOUND_DROPPED, SilenceSend.dropped(nothing.copy(shortSound = true)))
    }

    @Test
    fun aYesIsExpectedForAMinuteAfterAReadBackPlayed() {
        assertFalse(SilenceSend.yesExpected(null, 5_000))
        assertTrue(SilenceSend.yesExpected(1_000, 1_000))
        assertTrue(SilenceSend.yesExpected(1_000, 60_999))
        assertFalse(SilenceSend.yesExpected(1_000, 61_000))
        assertFalse(SilenceSend.yesExpected(5_000, 1_000))
    }
}
