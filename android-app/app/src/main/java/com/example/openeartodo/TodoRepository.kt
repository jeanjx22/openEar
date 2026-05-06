package com.example.openeartodo

import androidx.lifecycle.LiveData

class TodoRepository(private val todoDao: TodoDao) {
    val allTodos: LiveData<List<TodoItem>> = todoDao.getAllTodos()

    suspend fun insert(todo: TodoItem) {
        todoDao.insert(todo)
    }

    suspend fun update(todo: TodoItem) {
        todoDao.update(todo)
    }

    suspend fun delete(todo: TodoItem) {
        todoDao.delete(todo)
    }
}
