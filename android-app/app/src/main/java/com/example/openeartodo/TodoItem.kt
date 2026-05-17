package com.example.openeartodo

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "todoitem")
data class TodoItem(
    val text: String,
    val createdAt: Long = System.currentTimeMillis(),
    var isCompleted: Boolean = false,
    val completedAt: Long? = null,
    val eventAt: Long? = null,
    val reminderAt: Long? = null,
    val reminderType: String? = null,
    val alarmAt: Long? = null,
    val recurrence: String? = null,
    val snoozedUntil: Long? = null,
    val sourceGmailId: String? = null,
    val sourceRfc822Id: String? = null,
    val sourceEmailSummary: String? = null,
    @PrimaryKey(autoGenerate = true) val id: Long = 0
)
