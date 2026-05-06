package com.example.openeartodo

import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.Observer
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {
    private lateinit var todoAdapter: TodoAdapter
    private lateinit var todoViewModel: TodoViewModel
    private lateinit var todoInput: EditText

    private val emailSelector = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { /* Room LiveData auto-refreshes */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)

        NotificationHelper.createNotificationChannel(this)
        todoInput = findViewById(R.id.todoInput)

        setupTodoList()
        setupListeners()
        scheduleEmailCheck()
    }

    override fun onResume() {
        super.onResume()
        checkEmailsOnOpen()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Settings")
            .setIcon(android.R.drawable.ic_menu_preferences)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == 1) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    private fun setupTodoList() {
        val todoList: RecyclerView = findViewById(R.id.todoList)
        todoList.layoutManager = LinearLayoutManager(this)

        todoAdapter = TodoAdapter(mutableListOf()) { todo, isChecked ->
            todoViewModel.update(todo.copy(isCompleted = isChecked))
        }
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
                todoViewModel.insert(TodoItem(text = text))
                todoInput.setText("")
                Toast.makeText(this, "Added: $text", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Please enter some text", Toast.LENGTH_SHORT).show()
            }
        }

        findViewById<Button>(R.id.btnEmails).setOnClickListener {
            emailSelector.launch(Intent(this, EmailSelectorActivity::class.java))
        }
    }

    private fun checkEmailsOnOpen() {
        if (Prefs.getGmailAccount(this) == null || Prefs.getLlmApiKey(this).isBlank()) return

        lifecycleScope.launch {
            try {
                val count = EmailProcessor.processNewEmails(applicationContext)
                if (count > 0) {
                    Toast.makeText(
                        this@MainActivity,
                        "Found $count new todo(s) from email",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            } catch (_: Exception) { }
        }
    }

    private fun scheduleEmailCheck() {
        val request = PeriodicWorkRequestBuilder<EmailProcessingWorker>(1, TimeUnit.HOURS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .build()

        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "email_check",
            ExistingPeriodicWorkPolicy.KEEP,
            request
        )
    }
}
