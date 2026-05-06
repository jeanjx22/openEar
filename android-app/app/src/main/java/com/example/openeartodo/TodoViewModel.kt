package com.example.openeartodo

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

class TodoViewModel(application: Application) : AndroidViewModel(application) {
    private val repository: TodoRepository
    val allTodos: LiveData<List<TodoItem>>

    init {
        val todoDao = TodoDatabase.getInstance(application).todoDao()
        repository = TodoRepository(todoDao)
        allTodos = repository.allTodos
    }

    fun insert(todo: TodoItem) {
        viewModelScope.launch { repository.insert(todo) }
    }

    fun update(todo: TodoItem) {
        viewModelScope.launch { repository.update(todo) }
    }

    fun delete(todo: TodoItem) {
        viewModelScope.launch { repository.delete(todo) }
    }
}
