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
            val report = EmailProcessor.processNewEmails(applicationContext)
            if (report.todosExtracted > 0) {
                NotificationHelper.showNotification(
                    applicationContext,
                    "New TODOs from email",
                    "Extracted ${report.todosExtracted} new todo(s) from ${report.newProcessed} email(s)"
                )
            }
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
