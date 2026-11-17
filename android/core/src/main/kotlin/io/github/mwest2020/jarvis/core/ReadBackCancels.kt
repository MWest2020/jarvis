package io.github.mwest2020.jarvis.core

/**
 * The read-back that is playing, and the cancel that still has to reach the bridge (change
 * jarvis-streaming-speech, S6). Thread-safe: the main thread starts and ends playback, a turn
 * on an IO thread asks for a cancel that is still due before it sends anything.
 *
 * A cancel is kept until the bridge has answered it, and sent again before the next turn: a
 * spoken yes may never reach the bridge while an unheard read-back is still pending there. It
 * is dropped after [ttlMs], counted from when the read-back arrived: by then the bridge (and
 * the agent host, after `expires_s`, at most 60 seconds from even earlier) no longer has it.
 */
class ReadBackCancels(private val ttlMs: Long = ReadBack.PENDING_MS) {
    /** One read-back: its project, its id, and when its reply arrived (a monotonic clock). */
    data class Item(val project: String, val id: String, val sinceMs: Long)

    private var playing: Item? = null
    private var queued: Item? = null

    /** A reply that waits for a spoken yes starts playing. */
    @Synchronized
    fun started(project: String, id: String, nowMs: Long) {
        playing = Item(project, id, nowMs)
    }

    /** The read-back played to its end: it was heard, nothing to cancel. */
    @Synchronized
    fun played() {
        playing = null
    }

    /**
     * The read-back ended any other way (a tap, a cut stream, a playback error): it is queued
     * for a cancel, and returned to be sent now. Null when no read-back was playing.
     */
    @Synchronized
    fun notPlayed(nowMs: Long): Item? {
        val item = playing ?: return null
        playing = null
        queued = item
        return due(nowMs)
    }

    /** The cancel that still has to reach the bridge, or null; one past [ttlMs] is dropped. */
    @Synchronized
    fun due(nowMs: Long): Item? {
        val item = queued ?: return null
        if (nowMs - item.sinceMs >= ttlMs) {
            queued = null
            return null
        }
        return item
    }

    /** The bridge answered the cancel for [item]; a newer one stays queued. */
    @Synchronized
    fun sent(item: Item) {
        if (queued == item) queued = null
    }

    /**
     * Before a turn is sent: a cancel that is still due goes out first ([send], blocking). When
     * the bridge answers, the turn may go; when it does not, the turn is refused (the audio is
     * not uploaded) and the cancel stays queued. Null means the turn may go.
     */
    fun clearBeforeTurn(nowMs: Long, send: (Item) -> CancelOutcome): TurnFailure? {
        val item = due(nowMs) ?: return null
        return when (send(item)) {
            is CancelOutcome.Done -> {
                sent(item)
                null
            }
            is CancelOutcome.Failed -> TurnFailure(FailureKind.NETWORK, ReadBack.TURN_REFUSED)
        }
    }

    /**
     * What is still open when the screen goes away: a read-back that is playing (it will not
     * play to its end now) or a cancel that has not been answered. It is queued as well.
     */
    @Synchronized
    fun outstanding(nowMs: Long): Item? = notPlayed(nowMs) ?: due(nowMs)
}
