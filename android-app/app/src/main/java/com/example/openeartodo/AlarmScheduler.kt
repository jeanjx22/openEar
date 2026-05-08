package com.example.openeartodo

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build

object AlarmScheduler {

    private const val REQ_NOTIFY = 0
    private const val REQ_ALARM = 100000

    fun schedule(context: Context, todo: TodoItem) {
        cancel(context, todo.id)

        if (todo.reminderAt != null) {
            scheduleOne(context, todo.id, todo.reminderAt, todo.text, "notification", REQ_NOTIFY)
        }
        if (todo.alarmAt != null) {
            scheduleOne(context, todo.id, todo.alarmAt, todo.text, "alarm", REQ_ALARM)
        }
    }

    private fun scheduleOne(context: Context, todoId: Long, triggerAt: Long, text: String, type: String, reqOffset: Int) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = Intent(context, AlarmReceiver::class.java).apply {
            putExtra("title", "TODO Reminder")
            putExtra("message", text)
            putExtra("todoId", todoId)
            putExtra("type", type)
        }
        val requestCode = todoId.toInt() + reqOffset
        val pending = PendingIntent.getBroadcast(
            context, requestCode, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarmManager.canScheduleExactAlarms()) {
            alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
        } else {
            alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
        }
    }

    fun cancel(context: Context, todoId: Long) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = Intent(context, AlarmReceiver::class.java)

        for (reqOffset in listOf(REQ_NOTIFY, REQ_ALARM)) {
            val pending = PendingIntent.getBroadcast(
                context, todoId.toInt() + reqOffset, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            alarmManager.cancel(pending)
        }
    }
}
