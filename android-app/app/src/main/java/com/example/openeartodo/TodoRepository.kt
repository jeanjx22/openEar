package com.example.openeartodo

import androidx.lifecycle.LiveData

class TodoRepository(private val todoDao: TodoDao) {
    val activeTodos: LiveData<List<TodoItem>> = todoDao.getActiveTodos()

    suspend fun insert(todo: TodoItem) {
        todoDao.insert(todo)
    }

    suspend fun update(todo: TodoItem) {
        todoDao.update(todo)
    }

    suspend fun delete(todo: TodoItem) {
        todoDao.delete(todo)
    }

    suspend fun cleanupOldCompleted() {
        val sevenDaysAgo = System.currentTimeMillis() - 7 * 24 * 60 * 60 * 1000L
        todoDao.deleteCompletedBefore(sevenDaysAgo)
    }
}
