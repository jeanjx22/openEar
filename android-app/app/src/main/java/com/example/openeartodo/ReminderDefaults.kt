package com.example.openeartodo

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

object ReminderDefaults {

    private val isoFormats = arrayOf(
        "yyyy-MM-dd'T'HH:mm:ss",
        "yyyy-MM-dd'T'HH:mm",
        "yyyy-MM-dd"
    )

    fun parseEventTime(isoString: String?): Long? {
        if (isoString.isNullOrBlank()) return null
        for (fmt in isoFormats) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US)
                sdf.timeZone = TimeZone.getDefault()
                val date = sdf.parse(isoString) ?: continue
                return date.time
            } catch (_: Exception) { }
        }
        return null
    }

    fun defaultNotificationTime(eventAt: Long): Long {
        val cal = Calendar.getInstance()
        cal.timeInMillis = eventAt
        cal.add(Calendar.DAY_OF_YEAR, -1)
        cal.set(Calendar.HOUR_OF_DAY, 17)
        cal.set(Calendar.MINUTE, 0)
        cal.set(Calendar.SECOND, 0)
        cal.set(Calendar.MILLISECOND, 0)
        val result = cal.timeInMillis
        return if (result > System.currentTimeMillis()) result else eventAt
    }

    fun defaultAlarmTime(eventAt: Long): Long {
        val result = eventAt - 60 * 60 * 1000
        return if (result > System.currentTimeMillis()) result else eventAt
    }
}
