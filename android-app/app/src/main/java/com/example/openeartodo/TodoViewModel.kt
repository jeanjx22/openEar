package com.example.openeartodo

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch
import java.util.Calendar

class TodoViewModel(application: Application) : AndroidViewModel(application) {
    private val repository: TodoRepository
    val activeTodos: LiveData<List<TodoItem>>

    init {
        val todoDao = TodoDatabase.getInstance(application).todoDao()
        repository = TodoRepository(todoDao)
        activeTodos = repository.activeTodos
        viewModelScope.launch { repository.cleanupOldCompleted() }
    }

    fun insert(todo: TodoItem) {
        viewModelScope.launch { repository.insert(todo) }
    }

    fun complete(todo: TodoItem) {
        viewModelScope.launch {
            repository.update(todo.copy(isCompleted = true, completedAt = System.currentTimeMillis()))
            if (todo.recurrence != null && todo.eventAt != null) {
                createNextRecurrence(todo)
            }
        }
    }

    fun snooze(todo: TodoItem, durationMs: Long) {
        val snoozedUntil = System.currentTimeMillis() + durationMs
        viewModelScope.launch {
            AlarmScheduler.cancel(getApplication(), todo.id)
            repository.update(todo.copy(snoozedUntil = snoozedUntil, reminderAt = snoozedUntil, reminderType = "notification"))
            AlarmScheduler.schedule(getApplication(), todo.copy(reminderAt = snoozedUntil))
        }
    }

    fun unsnooze(todo: TodoItem) {
        viewModelScope.launch {
            repository.update(todo.copy(snoozedUntil = null))
        }
    }

    fun update(todo: TodoItem) {
        viewModelScope.launch { repository.update(todo) }
    }

    fun delete(todo: TodoItem) {
        viewModelScope.launch { repository.delete(todo) }
    }

    private suspend fun createNextRecurrence(original: TodoItem) {
        val nextEventAt = computeNextOccurrence(original.eventAt!!, original.recurrence!!)
        val next = TodoItem(
            text = original.text,
            eventAt = nextEventAt,
            reminderAt = ReminderDefaults.defaultNotificationTime(nextEventAt),
            alarmAt = ReminderDefaults.defaultAlarmTime(nextEventAt),
            reminderType = "both",
            recurrence = original.recurrence,
            sourceGmailId = original.sourceGmailId,
            sourceRfc822Id = original.sourceRfc822Id,
            sourceEmailSummary = original.sourceEmailSummary
        )
        repository.insert(next)
        AlarmScheduler.schedule(getApplication(), next)
    }

    private fun computeNextOccurrence(currentEventAt: Long, recurrence: String): Long {
        val cal = Calendar.getInstance()
        cal.timeInMillis = currentEventAt
        when (recurrence) {
            "daily" -> cal.add(Calendar.DAY_OF_YEAR, 1)
            "weekly" -> cal.add(Calendar.WEEK_OF_YEAR, 1)
            "biweekly" -> cal.add(Calendar.WEEK_OF_YEAR, 2)
            "monthly" -> cal.add(Calendar.MONTH, 1)
        }
        return cal.timeInMillis
    }
}
