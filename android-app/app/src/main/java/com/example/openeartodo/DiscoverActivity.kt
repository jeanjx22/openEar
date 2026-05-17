package com.example.openeartodo

import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.gms.auth.UserRecoverableAuthException
import kotlinx.coroutines.launch

class DiscoverActivity : AppCompatActivity() {

    private lateinit var adapter: DiscoverAdapter
    private lateinit var btnScan: Button
    private lateinit var btnAddSelected: Button
    private lateinit var progress: ProgressBar
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_discover)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Discover Senders"
        toolbar.setNavigationOnClickListener { finish() }

        adapter = DiscoverAdapter()
        val recycler: RecyclerView = findViewById(R.id.resultList)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        btnScan = findViewById(R.id.btnScan)
        btnAddSelected = findViewById(R.id.btnAddSelected)
        progress = findViewById(R.id.progress)
        tvStatus = findViewById(R.id.tvStatus)

        findViewById<View>(R.id.bottomBar).visibility = View.VISIBLE

        btnScan.setOnClickListener { startScan() }
        btnAddSelected.setOnClickListener { addSelected() }
    }

    private fun startScan() {
        val account = Prefs.getGmailAccount(this)
        val apiKey = Prefs.getLlmApiKey(this)
        if (account == null || apiKey.isBlank()) {
            Toast.makeText(this, "Set up Gmail and API key in Settings first", Toast.LENGTH_LONG).show()
            return
        }

        btnScan.isEnabled = false
        btnAddSelected.visibility = View.GONE
        progress.visibility = View.VISIBLE
        progress.isIndeterminate = true
        tvStatus.text = "Fetching emails from the past week..."

        val provider = Prefs.getLlmProvider(this)

        lifecycleScope.launch {
            try {
                val db = TodoDatabase.getInstance(applicationContext)
                val token = GmailClient.getAccessToken(this@DiscoverActivity, account)

                // Get exclusion lists
                val tracked = db.allowedSenderDao().getAllSync().map { it.pattern }
                val ignored = db.ignoredSenderDao().getAllPatterns()
                val excludeList = tracked + ignored

                // Phase 1: Fetch all emails from past week
                tvStatus.text = "Fetching emails..."
                val allEmails = mutableListOf<GmailClient.EmailInfo>()
                var pageToken: String? = null
                do {
                    val page = GmailClient.fetchEmails(token, query = "newer_than:7d", pageToken = pageToken)
                    allEmails.addAll(page.emails)
                    pageToken = page.nextPageToken
                    tvStatus.text = "Fetched ${allEmails.size} emails..."
                } while (pageToken != null)

                // Filter out tracked, ignored, and already-processed senders
                val untracked = allEmails.filter { email ->
                    !EmailProcessor.matchesSenderList(email.sender, excludeList)
                }

                if (untracked.isEmpty()) {
                    tvStatus.text = "No new senders found in ${allEmails.size} emails."
                    btnScan.isEnabled = true
                    progress.visibility = View.GONE
                    return@launch
                }

                // Phase 2: Batch classify
                tvStatus.text = "Classifying ${untracked.size} emails..."
                progress.isIndeterminate = false
                progress.max = untracked.size

                val important = mutableListOf<GmailClient.EmailInfo>()
                var classifyErrors = 0
                for ((i, batch) in untracked.chunked(10).withIndex()) {
                    try {
                        val triples = batch.map { Triple(it.sender, it.subject, it.snippet) }
                        val results = LlmClient.classifyEmailsBatch(apiKey, provider, triples)
                        for (j in results.indices) {
                            if (results[j]) important.add(batch[j])
                        }
                    } catch (e: Exception) {
                        classifyErrors++
                        kotlinx.coroutines.delay(2000)
                    }
                    progress.progress = minOf((i + 1) * 10, untracked.size)
                    tvStatus.text = "Classifying... ${important.size} important so far" +
                        if (classifyErrors > 0) " ($classifyErrors retries skipped)" else ""
                }

                if (important.isEmpty()) {
                    tvStatus.text = "No important untracked emails found in ${allEmails.size} emails."
                    btnScan.isEnabled = true
                    progress.visibility = View.GONE
                    return@launch
                }

                // Phase 3: Extract TODOs from important emails
                tvStatus.text = "Extracting action items from ${important.size} emails..."
                progress.progress = 0
                progress.max = important.size

                val groups = mutableMapOf<String, MutableList<DiscoverEmail>>()
                val senderDisplayNames = mutableMapOf<String, String>()

                var extractErrors = 0
                for ((i, email) in important.withIndex()) {
                    try {
                        val body = GmailClient.fetchEmailBody(token, email.gmailId)
                        val result = LlmClient.extractTodosWithSummary(
                            apiKey, provider, email.sender, email.subject, body
                        )

                        val pattern = EmailProcessor.extractSenderPattern(email.sender)
                        senderDisplayNames.putIfAbsent(pattern, email.sender)

                        groups.getOrPut(pattern) { mutableListOf() }.add(
                            DiscoverEmail(
                                gmailId = email.gmailId,
                                rfc822MsgId = email.rfc822MsgId,
                                subject = email.subject,
                                sender = email.sender,
                                todos = result.todos,
                                summary = result.summary,
                                body = body
                            )
                        )
                    } catch (e: Exception) {
                        extractErrors++
                        kotlinx.coroutines.delay(2000)
                    }
                    progress.progress = i + 1
                    tvStatus.text = "Processing ${i + 1}/${important.size}..." +
                        if (extractErrors > 0) " ($extractErrors skipped)" else ""
                }

                // Build display groups (only those with action items)
                val discoverGroups = groups.mapNotNull { (pattern, emails) ->
                    val allTodos = emails.flatMap { it.todos }
                    if (allTodos.isEmpty()) return@mapNotNull null
                    DiscoverGroup(
                        senderPattern = pattern,
                        senderDisplay = senderDisplayNames[pattern] ?: pattern,
                        emailCount = emails.size,
                        todos = allTodos.map { it.text },
                        emailDetails = emails
                    )
                }.sortedByDescending { it.todos.size }

                if (discoverGroups.isEmpty()) {
                    tvStatus.text = "No action items found from untracked senders."
                } else {
                    tvStatus.text = "Found ${discoverGroups.size} sender(s) with action items. Select the ones to track:"
                    btnAddSelected.visibility = View.VISIBLE
                }
                adapter.setItems(discoverGroups)

            } catch (e: UserRecoverableAuthException) {
                Toast.makeText(this@DiscoverActivity, "Gmail auth required", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                tvStatus.text = "Scan failed: ${e.message}"
            } finally {
                btnScan.isEnabled = true
                btnScan.text = "Scan Again"
                progress.visibility = View.GONE
            }
        }
    }

    private fun addSelected() {
        val selected = adapter.getSelectedGroups()
        val unselectedPatterns = adapter.getUnselectedPatterns()

        if (selected.isEmpty()) {
            Toast.makeText(this, "Select at least one sender", Toast.LENGTH_SHORT).show()
            return
        }

        lifecycleScope.launch {
            val db = TodoDatabase.getInstance(applicationContext)

            // Add selected senders to tracked list + their TODOs
            for (group in selected) {
                db.allowedSenderDao().insert(
                    AllowedSender(pattern = group.senderPattern, label = group.senderPattern)
                )
                for (email in group.emailDetails) {
                    val summary = "From: ${email.sender}\nSubject: ${email.subject}\n\n${email.summary}\n\n--- Full Email ---\n${email.body.take(5000)}"
                    for (extracted in email.todos) {
                        val eventAt = ReminderDefaults.parseEventTime(extracted.eventTime)
                        val todo = TodoItem(
                            text = extracted.text,
                            eventAt = eventAt,
                            reminderAt = eventAt?.let { ReminderDefaults.defaultNotificationTime(it) },
                            alarmAt = eventAt?.let { ReminderDefaults.defaultAlarmTime(it) },
                            reminderType = if (eventAt != null) "both" else null,
                            sourceGmailId = email.gmailId,
                            sourceRfc822Id = email.rfc822MsgId,
                            sourceEmailSummary = summary
                        )
                        db.todoDao().insert(todo)
                        if (eventAt != null) AlarmScheduler.schedule(applicationContext, todo)
                    }
                    db.processedEmailDao().insert(ProcessedEmail(gmailId = email.gmailId))
                }
            }

            // Add unselected senders to ignored list
            for (pattern in unselectedPatterns) {
                db.ignoredSenderDao().insert(IgnoredSender(pattern = pattern))
            }

            Toast.makeText(
                this@DiscoverActivity,
                "Tracking ${selected.size} sender(s), ignored ${unselectedPatterns.size}",
                Toast.LENGTH_SHORT
            ).show()
            finish()
        }
    }
}
