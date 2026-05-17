package com.example.openeartodo

import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.Observer
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.launch

class IgnoredSendersActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ignored_senders)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Excluded Senders"
        toolbar.setNavigationOnClickListener { finish() }

        val db = TodoDatabase.getInstance(this)
        val recycler: RecyclerView = findViewById(R.id.senderList)
        recycler.layoutManager = LinearLayoutManager(this)

        val tvEmpty: TextView = findViewById(R.id.tvEmpty)

        val adapter = IgnoredSenderAdapter { sender ->
            MaterialAlertDialogBuilder(this)
                .setTitle("Remove from excluded?")
                .setMessage("${sender.pattern} will be re-evaluated on the next sync.")
                .setPositiveButton("Remove") { _, _ ->
                    lifecycleScope.launch {
                        db.ignoredSenderDao().delete(sender.pattern)
                        Toast.makeText(this@IgnoredSendersActivity, "Removed", Toast.LENGTH_SHORT).show()
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        recycler.adapter = adapter

        db.ignoredSenderDao().getAll().observe(this, Observer { senders ->
            adapter.setItems(senders)
            tvEmpty.visibility = if (senders.isEmpty()) View.VISIBLE else View.GONE
            recycler.visibility = if (senders.isEmpty()) View.GONE else View.VISIBLE
        })
    }
}
