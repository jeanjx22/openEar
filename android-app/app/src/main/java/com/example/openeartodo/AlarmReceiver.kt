package com.example.openeartodo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // Handle notification/alarm triggers here
        NotificationHelper.showNotification(
            context,
            intent.getStringExtra("title") ?: "OpenEar Reminder",
            intent.getStringExtra("message") ?: "Reminder time!"
        )
    }
}