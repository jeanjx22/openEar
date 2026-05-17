package com.example.openeartodo

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Calendar

class SnoozeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val todoId = intent.getLongExtra("todoId", -1)
        if (todoId == -1L) return

        val duration = intent.getLongExtra("duration", 0)
        val label = intent.getStringExtra("label") ?: ""

        // Dismiss the notification
        val mgr = context.getSystemService(NotificationManager::class.java)
        mgr.cancelAll()

        CoroutineScope(Dispatchers.IO).launch {
            val db = TodoDatabase.getInstance(context)
            val todo = db.todoDao().getById(todoId) ?: return@launch

            val snoozedUntil = if (label == "tomorrow") {
                val cal = Calendar.getInstance()
                cal.add(Calendar.DAY_OF_YEAR, 1)
                cal.set(Calendar.HOUR_OF_DAY, 8)
                cal.set(Calendar.MINUTE, 0)
                cal.set(Calendar.SECOND, 0)
                cal.timeInMillis
            } else {
                System.currentTimeMillis() + duration
            }

            AlarmScheduler.cancel(context, todoId)
            val updated = todo.copy(
                snoozedUntil = snoozedUntil,
                reminderAt = snoozedUntil,
                reminderType = "notification"
            )
            db.todoDao().update(updated)
            AlarmScheduler.schedule(context, updated)
        }
    }
}
