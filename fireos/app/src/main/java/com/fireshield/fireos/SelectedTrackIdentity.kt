package com.fireshield.fireos

import androidx.media3.common.Tracks

object SelectedTrackIdentity {
    fun selectedId(tracks: Tracks, type: Int): String? {
        for (group in tracks.groups) {
            if (group.type != type) continue
            for (index in 0 until group.length) {
                if (!group.isTrackSelected(index)) continue
                val format = group.getTrackFormat(index)
                return format.id ?: listOf(type, format.language, format.label,
                    format.codecs, format.channelCount, format.sampleRate).joinToString(":")
            }
        }
        return null
    }
}
