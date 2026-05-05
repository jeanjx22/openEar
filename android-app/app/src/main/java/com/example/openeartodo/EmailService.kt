package com.example.openeartodo

import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.work.WorkManager

class EmailService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Start email processing worker
        WorkManager.getInstance(this).enqueue(EmailProcessingWorker.createWorkRequest())
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null
}