package com.example.openeartodo

import android.os.Bundle
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.Observer
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.floatingactionbutton.FloatingActionButton
import kotlinx.coroutines.launch

class TrackedSendersActivity : AppCompatActivity() {

    private lateinit var adapter: SenderAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_tracked_senders)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Tracked Senders"
        toolbar.setNavigationOnClickListener { finish() }

        val db = TodoDatabase.getInstance(this)

        adapter = SenderAdapter { sender ->
            MaterialAlertDialogBuilder(this)
                .setTitle("Remove sender?")
                .setMessage("Stop tracking emails from ${sender.pattern}?")
                .setPositiveButton("Remove") { _, _ ->
                    lifecycleScope.launch {
                        db.allowedSenderDao().delete(sender)
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        val recycler: RecyclerView = findViewById(R.id.senderList)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        db.allowedSenderDao().getAll().observe(this, Observer { senders ->
            adapter.setItems(senders)
            if (senders.isEmpty()) {
                Toast.makeText(this, "No tracked senders yet", Toast.LENGTH_SHORT).show()
            }
        })

        findViewById<FloatingActionButton>(R.id.fabAdd).setOnClickListener {
            showAddDialog()
        }
    }

    private fun showAddDialog() {
        val input = EditText(this).apply {
            hint = "e.g. *@school.edu or teacher@school.edu"
            setPadding(64, 32, 64, 16)
        }

        MaterialAlertDialogBuilder(this)
            .setTitle("Add sender pattern")
            .setMessage("Use *@domain.com to track all emails from a domain, or a specific address.")
            .setView(input)
            .setPositiveButton("Add") { _, _ ->
                val pattern = input.text.toString().trim()
                if (pattern.isNotEmpty()) {
                    val label = pattern.removePrefix("*@").removePrefix("*")
                    lifecycleScope.launch {
                        TodoDatabase.getInstance(applicationContext)
                            .allowedSenderDao()
                            .insert(AllowedSender(pattern = pattern, label = label))
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
