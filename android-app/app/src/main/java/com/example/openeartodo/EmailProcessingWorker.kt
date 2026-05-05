package com.example.openeartodo

import android.content.Context
import androidx.work.Worker
import androidx.work.WorkerParameters

class EmailProcessingWorker(
    context: Context,
    workerParams: WorkerParameters
) : Worker(context, workerParams) {
    
    override fun doWork(): Result {
        val notificationTitle = "New Task"
        val notificationMessage = "Reminder added to your list"
        NotificationHelper.showNotification(applicationContext, notificationTitle, notificationMessage)
        
        return Result.success()
    }
    
    companion object {
        fun createWorkRequest() = 
            androidx.work.OneTimeWorkRequestBuilder<EmailProcessingWorker>().build()
    }
}