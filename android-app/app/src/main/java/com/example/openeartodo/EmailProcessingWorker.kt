package com.example.openeartodo

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

class EmailProcessingWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        return try {
            val count = EmailProcessor.processNewEmails(applicationContext)
            if (count > 0) {
                NotificationHelper.showNotification(
                    applicationContext,
                    "New TODOs from email",
                    "Extracted $count new todo(s)"
                )
            }
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
