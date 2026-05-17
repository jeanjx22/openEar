package com.example.openeartodo

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val title = intent.getStringExtra("title") ?: "OpenEar Reminder"
        val message = intent.getStringExtra("message") ?: "Reminder time!"
        val type = intent.getStringExtra("type") ?: "notification"
        val todoId = intent.getLongExtra("todoId", -1)

        val snooze1h = Intent(context, SnoozeReceiver::class.java).apply {
            putExtra("todoId", todoId)
            putExtra("duration", 60 * 60 * 1000L)
            putExtra("label", "1hr")
        }
        val snoozeTomorrow = Intent(context, SnoozeReceiver::class.java).apply {
            putExtra("todoId", todoId)
            putExtra("duration", 0L)
            putExtra("label", "tomorrow")
        }

        val pi1h = PendingIntent.getBroadcast(context, (todoId * 10 + 1).toInt(), snooze1h,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val piTomorrow = PendingIntent.getBroadcast(context, (todoId * 10 + 2).toInt(), snoozeTomorrow,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        if (type == "alarm") {
            NotificationHelper.showAlarm(context, title, message, pi1h, piTomorrow)
        } else {
            NotificationHelper.showNotification(context, title, message, pi1h, piTomorrow)
        }
    }
}
