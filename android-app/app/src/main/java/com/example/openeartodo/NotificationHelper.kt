package com.example.openeartodo

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat

object NotificationHelper {
    private const val CHANNEL_ID = "openear_reminders"
    private const val CHANNEL_NAME = "OpenEar Reminders"
    
    fun createNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                CHANNEL_NAME,
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Reminders for appointments and tasks"
            }
            
            val notificationManager = context.getSystemService(
                NotificationManager::class.java
            )
            notificationManager.createNotificationChannel(channel)
        }
    }
    
    fun showNotification(context: Context, title: String, message: String) {
        val notificationManager = context.getSystemService(
            NotificationManager::class.java
        )
        
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(message)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        
        notificationManager.notify(System.currentTimeMillis().toInt(), notification)
    }
}