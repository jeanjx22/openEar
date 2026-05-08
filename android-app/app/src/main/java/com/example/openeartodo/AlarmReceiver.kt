package com.example.openeartodo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val title = intent.getStringExtra("title") ?: "OpenEar Reminder"
        val message = intent.getStringExtra("message") ?: "Reminder time!"
        val type = intent.getStringExtra("type") ?: "notification"

        if (type == "alarm") {
            NotificationHelper.showAlarm(context, title, message)
        } else {
            NotificationHelper.showNotification(context, title, message)
        }
    }
}
