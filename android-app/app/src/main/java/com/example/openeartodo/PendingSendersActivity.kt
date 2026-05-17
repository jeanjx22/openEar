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
import kotlinx.coroutines.launch

class PendingSendersActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pending_senders)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Review New Senders"
        toolbar.setNavigationOnClickListener { finish() }

        val recycler: RecyclerView = findViewById(R.id.pendingList)
        recycler.layoutManager = LinearLayoutManager(this)

        val tvEmpty: TextView = findViewById(R.id.tvEmpty)
        val db = TodoDatabase.getInstance(this)

        val adapter = PendingAdapter(
            onTrack = { sender ->
                lifecycleScope.launch {
                    db.allowedSenderDao().insert(AllowedSender(pattern = sender.pattern, label = sender.pattern))
                    db.pendingSenderDao().delete(sender)
                    Toast.makeText(this@PendingSendersActivity, "Tracking ${sender.pattern}", Toast.LENGTH_SHORT).show()
                }
            },
            onIgnore = { sender ->
                lifecycleScope.launch {
                    db.ignoredSenderDao().insert(IgnoredSender(pattern = sender.pattern))
                    db.pendingSenderDao().delete(sender)
                    Toast.makeText(this@PendingSendersActivity, "Ignored ${sender.pattern}", Toast.LENGTH_SHORT).show()
                }
            }
        )
        recycler.adapter = adapter

        db.pendingSenderDao().getAll().observe(this, Observer { senders ->
            adapter.setItems(senders)
            tvEmpty.visibility = if (senders.isEmpty()) View.VISIBLE else View.GONE
            recycler.visibility = if (senders.isEmpty()) View.GONE else View.VISIBLE
        })
    }
}
