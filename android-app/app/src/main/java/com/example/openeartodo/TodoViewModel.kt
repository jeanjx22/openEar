package com.example.openeartodo

import android.app.Application
import android.widget.Toast
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.viewModelScope

class TodoViewModel(application: Application) : AndroidViewModel(application) {
    private val repository: TodoRepository
    val allTodos: LiveData<List<TodoItem>>

    init {
        val todoDao = TodoDatabase.getInstance(application).todoDao()
        repository = TodoRepository(todoDao)
        allTodos = repository.allTodos
    }

    fun insert(todo: TodoItem) {
        repository.insert(todo, viewModelScope)
    }

    fun update(todo: TodoItem) {
        repository.update(todo, viewModelScope)
    }

    fun delete(todo: TodoItem) {
        repository.delete(todo, viewModelScope)
    }
}