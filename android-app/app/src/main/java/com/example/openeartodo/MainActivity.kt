package com.example.openeartodo

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.Observer
import androidx.lifecycle.ViewModelProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

class MainActivity : AppCompatActivity() {
    private lateinit var todoAdapter: TodoAdapter
    private lateinit var todoViewModel: TodoViewModel
    private lateinit var todoInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)

        NotificationHelper.createNotificationChannel(this)

        todoInput = findViewById(R.id.todoInput)

        setupTodoList()
        setupListeners()
    }

    private fun setupTodoList() {
        val todoList: RecyclerView = findViewById(R.id.todoList)
        todoList.layoutManager = LinearLayoutManager(this)

        todoAdapter = TodoAdapter(mutableListOf())
        todoList.adapter = todoAdapter

        todoViewModel = ViewModelProvider(this).get(TodoViewModel::class.java)

        todoViewModel.allTodos.observe(this, Observer { todos ->
            todoAdapter.setItems(todos)
        })
    }

    private fun setupListeners() {
        findViewById<Button>(R.id.addButton).setOnClickListener {
            val text = todoInput.text.toString().trim()
            if (text.isNotEmpty()) {
                val newTodo = TodoItem(text = text)
                todoViewModel.insert(newTodo)
                todoInput.setText("")
                Toast.makeText(this, "Added: $text", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Please enter some text", Toast.LENGTH_SHORT).show()
            }
        }
    }
}