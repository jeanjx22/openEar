package com.example.openeartodo

import android.app.DatePickerDialog
import android.app.TimePickerDialog
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
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
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
        menu.add(0, 1, 0, "Discover Senders")
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 2, 1, "Tracked Senders")
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 3, 2, "Settings")
            .setIcon(android.R.drawable.ic_menu_preferences)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { startActivity(Intent(this, DiscoverActivity::class.java)); true }
            2 -> { startActivity(Intent(this, TrackedSendersActivity::class.java)); true }
            3 -> { startActivity(Intent(this, SettingsActivity::class.java)); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    private fun setupTodoList() {
        val todoList: RecyclerView = findViewById(R.id.todoList)
        todoList.layoutManager = LinearLayoutManager(this)

        todoAdapter = TodoAdapter(
            mutableListOf(),
            onCheckedChanged = { todo, isChecked ->
                if (isChecked) {
                    AlarmScheduler.cancel(this, todo.id)
                    todoViewModel.complete(todo)
                }
            },
            onAlarmClicked = { todo -> showReminderPicker(todo) }
        )
        todoList.adapter = todoAdapter

        todoViewModel = ViewModelProvider(this).get(TodoViewModel::class.java)

        todoViewModel.activeTodos.observe(this, Observer { todos ->
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

        findViewById<Button>(R.id.btnSync).setOnClickListener {
            manualSync()
        }
    }

    private fun manualSync() {
        val btnSync = findViewById<Button>(R.id.btnSync)
        if (Prefs.getGmailAccount(this) == null || Prefs.getLlmApiKey(this).isBlank()) {
            Toast.makeText(this, "Set up Gmail and API key in Settings first", Toast.LENGTH_LONG).show()
            return
        }

        btnSync.isEnabled = false
        btnSync.text = "Syncing..."

        val lookbackOverride = Prefs.getLookbackOverride(this).ifBlank { null }

        lifecycleScope.launch {
            try {
                val report = EmailProcessor.processNewEmails(applicationContext, lookbackOverride)
                showSyncReport(report)
            } catch (e: Exception) {
                Toast.makeText(this@MainActivity, "Sync failed: ${e.message}", Toast.LENGTH_LONG).show()
            } finally {
                btnSync.isEnabled = true
                btnSync.text = "SYNC"
            }
        }
    }

    private fun showReminderPicker(todo: TodoItem) {
        val hasReminder = (todo.reminderAt != null && todo.reminderAt > System.currentTimeMillis()) ||
                          (todo.alarmAt != null && todo.alarmAt > System.currentTimeMillis())
        if (hasReminder) {
            val timeFmt = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())
            val lines = mutableListOf<String>()
            if (todo.reminderAt != null) lines.add("Notification: ${timeFmt.format(Date(todo.reminderAt))}")
            if (todo.alarmAt != null) lines.add("Alarm: ${timeFmt.format(Date(todo.alarmAt))}")
            MaterialAlertDialogBuilder(this)
                .setTitle("Reminder")
                .setMessage(lines.joinToString("\n"))
                .setPositiveButton("Change") { _, _ -> pickReminderType(todo) }
                .setNeutralButton("Remove") { _, _ ->
                    AlarmScheduler.cancel(this, todo.id)
                    todoViewModel.update(todo.copy(reminderAt = null, reminderType = null, alarmAt = null))
                    Toast.makeText(this, "Reminders removed", Toast.LENGTH_SHORT).show()
                }
                .setNegativeButton("Cancel", null)
                .show()
        } else {
            pickReminderType(todo)
        }
    }

    private fun pickReminderType(todo: TodoItem) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Reminder type")
            .setItems(arrayOf("Notification only", "Alarm only", "Both (notification + alarm)")) { _, which ->
                when (which) {
                    0 -> pickDateTime("Notification time") { time ->
                        val updated = todo.copy(reminderAt = time, reminderType = "notification", alarmAt = null)
                        todoViewModel.update(updated)
                        AlarmScheduler.schedule(this, updated)
                        showConfirmation("Notification", time)
                    }
                    1 -> pickDateTime("Alarm time") { time ->
                        val updated = todo.copy(reminderAt = null, reminderType = "alarm", alarmAt = time)
                        todoViewModel.update(updated)
                        AlarmScheduler.schedule(this, updated)
                        showConfirmation("Alarm", time)
                    }
                    2 -> pickDateTime("Notification time") { notifyTime ->
                        pickDateTime("Alarm time") { alarmTime ->
                            val updated = todo.copy(reminderAt = notifyTime, reminderType = "both", alarmAt = alarmTime)
                            todoViewModel.update(updated)
                            AlarmScheduler.schedule(this, updated)
                            val fmt = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())
                            Toast.makeText(this,
                                "Notify: ${fmt.format(Date(notifyTime))}, Alarm: ${fmt.format(Date(alarmTime))}",
                                Toast.LENGTH_LONG).show()
                        }
                    }
                }
            }
            .show()
    }

    private fun pickDateTime(label: String, onPicked: (Long) -> Unit) {
        Toast.makeText(this, "Set $label", Toast.LENGTH_SHORT).show()
        val cal = Calendar.getInstance()
        DatePickerDialog(this, { _, year, month, day ->
            TimePickerDialog(this, { _, hour, minute ->
                cal.set(year, month, day, hour, minute, 0)
                cal.set(Calendar.MILLISECOND, 0)
                onPicked(cal.timeInMillis)
            }, cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), false).apply {
                setTitle("$label - pick time")
            }.show()
        }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).apply {
            setTitle("$label - pick date")
        }.show()
    }

    private fun showConfirmation(type: String, time: Long) {
        val fmt = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())
        Toast.makeText(this, "$type set for ${fmt.format(Date(time))}", Toast.LENGTH_SHORT).show()
    }

    private fun showSyncReport(report: SyncReport) {
        val sb = StringBuilder()
        sb.appendLine("Query: ${report.query}")
        sb.appendLine("Emails found: ${report.totalFetched}")
        sb.appendLine("Already processed: ${report.alreadyProcessed}")
        sb.appendLine("New processed: ${report.newProcessed}")
        sb.appendLine("TODOs extracted: ${report.todosExtracted}")
        if (report.errors > 0) sb.appendLine("Errors: ${report.errors}")
        if (report.senderBreakdown.isNotEmpty()) {
            sb.appendLine("\n--- By Sender ---")
            for ((sender, stats) in report.senderBreakdown) {
                sb.appendLine("$sender: ${stats.fetched} found, ${stats.processed} new, ${stats.todos} todos")
            }
        }

        MaterialAlertDialogBuilder(this)
            .setTitle("Sync Report")
            .setMessage(sb.toString())
            .setPositiveButton("OK", null)
            .show()
    }

    private fun checkEmailsOnOpen() {
        if (Prefs.getGmailAccount(this) == null || Prefs.getLlmApiKey(this).isBlank()) return

        lifecycleScope.launch {
            try {
                val report = EmailProcessor.processNewEmails(applicationContext)
                if (report.todosExtracted > 0) {
                    Toast.makeText(
                        this@MainActivity,
                        "Found ${report.todosExtracted} new todo(s) from email",
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
