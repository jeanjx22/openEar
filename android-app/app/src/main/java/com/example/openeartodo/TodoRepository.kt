package com.example.openeartodo

import androidx.lifecycle.LiveData
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class TodoRepository(private val todoDao: TodoDao) {
    val allTodos: LiveData<List<TodoItem>> = todoDao.getAllTodos()

    fun insert(todo: TodoItem, scope: CoroutineScope) {
        scope.launch(Dispatchers.IO) {
            todoDao.insert(todo)
        }
    }

    fun update(todo: TodoItem, scope: CoroutineScope) {
        scope.launch(Dispatchers.IO) {
            todoDao.update(todo)
        }
    }

    fun delete(todo: TodoItem, scope: CoroutineScope) {
        scope.launch(Dispatchers.IO) {
            todoDao.delete(todo)
        }
    }
}