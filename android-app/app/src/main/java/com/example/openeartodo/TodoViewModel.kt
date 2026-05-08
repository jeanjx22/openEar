package com.example.openeartodo

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

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
        }
    }

    fun update(todo: TodoItem) {
        viewModelScope.launch { repository.update(todo) }
    }

    fun delete(todo: TodoItem) {
        viewModelScope.launch { repository.delete(todo) }
    }
}
