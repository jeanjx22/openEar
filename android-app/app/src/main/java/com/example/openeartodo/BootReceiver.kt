package com.example.openeartodo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        CoroutineScope(Dispatchers.IO).launch {
            val db = TodoDatabase.getInstance(context)
            val now = System.currentTimeMillis()
            for (todo in db.todoDao().getActiveTodosSync()) {
                val hasReminder = (todo.reminderAt != null && todo.reminderAt > now) ||
                                  (todo.alarmAt != null && todo.alarmAt > now)
                if (hasReminder) {
                    AlarmScheduler.schedule(context, todo)
                }
            }
        }
    }
}
