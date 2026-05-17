package com.example.openeartodo

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.NotificationCompat

object NotificationHelper {
    private const val CHANNEL_NOTIFY = "openear_reminders"
    private const val CHANNEL_ALARM = "openear_alarms"

    fun createNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = context.getSystemService(NotificationManager::class.java)

            mgr.createNotificationChannel(NotificationChannel(
                CHANNEL_NOTIFY, "OpenEar Reminders",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Silent/brief reminders for tasks"
            })

            val alarmSound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            mgr.createNotificationChannel(NotificationChannel(
                CHANNEL_ALARM, "OpenEar Alarms",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Persistent alarms that ring until dismissed"
                setSound(alarmSound, AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build())
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 500, 200, 500, 200, 500)
            })
        }
    }

    fun showNotification(
        context: Context, title: String, message: String,
        snooze1h: PendingIntent? = null, snoozeTomorrow: PendingIntent? = null
    ) {
        val mgr = context.getSystemService(NotificationManager::class.java)
        val builder = NotificationCompat.Builder(context, CHANNEL_NOTIFY)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(message)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
        if (snooze1h != null) builder.addAction(0, "Snooze 1hr", snooze1h)
        if (snoozeTomorrow != null) builder.addAction(0, "Tomorrow 8am", snoozeTomorrow)
        mgr.notify(System.currentTimeMillis().toInt(), builder.build())
    }

    fun showAlarm(
        context: Context, title: String, message: String,
        snooze1h: PendingIntent? = null, snoozeTomorrow: PendingIntent? = null
    ) {
        val mgr = context.getSystemService(NotificationManager::class.java)
        val alarmSound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
        val builder = NotificationCompat.Builder(context, CHANNEL_ALARM)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(title)
            .setContentText(message)
            .setSound(alarmSound)
            .setVibrate(longArrayOf(0, 500, 200, 500, 200, 500))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setOngoing(true)
        if (snooze1h != null) builder.addAction(0, "Snooze 1hr", snooze1h)
        if (snoozeTomorrow != null) builder.addAction(0, "Tomorrow 8am", snoozeTomorrow)
        mgr.notify(System.currentTimeMillis().toInt(), builder.build())
    }
}
