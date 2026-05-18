package com.example.openeartodo

import android.content.Context
import com.google.android.gms.auth.UserRecoverableAuthException

data class SyncReport(
    val query: String,
    val totalFetched: Int,
    val alreadyProcessed: Int,
    val newProcessed: Int,
    val todosExtracted: Int,
    val errors: Int,
    val senderBreakdown: Map<String, SenderStats>,
    val authError: Boolean = false
)

data class SenderStats(
    var fetched: Int = 0,
    var skipped: Int = 0,
    var processed: Int = 0,
    var todos: Int = 0
)

object EmailProcessor {

    suspend fun processNewEmails(context: Context, lookbackOverride: String? = null): SyncReport {
        val emptyReport = SyncReport("", 0, 0, 0, 0, 0, emptyMap())
        val accounts = Prefs.getGmailAccounts(context)
        if (accounts.isEmpty()) return emptyReport
        val apiKey = Prefs.getLlmApiKey(context)
        if (apiKey.isBlank()) return emptyReport

        val provider = Prefs.getLlmProvider(context)
        val db = TodoDatabase.getInstance(context)
        val senders = db.allowedSenderDao().getAllSync()
        if (senders.isEmpty()) return emptyReport

        val lookback = lookbackOverride ?: getLookbackQuery(context)
        val query = buildGmailQuery(senders.map { it.pattern }, lookback)

        val breakdown = mutableMapOf<String, SenderStats>()
        var totalFetched = 0
        var alreadyProcessed = 0
        var newProcessed = 0
        var todosExtracted = 0
        var errors = 0
        var authError = false

        for (account in accounts) {
            val token = try {
                GmailClient.getAccessToken(context, account)
            } catch (_: Exception) {
                authError = true
                continue
            }

            var pageToken: String? = null
            do {
                val page = GmailClient.fetchEmails(token, query = query, pageToken = pageToken)
                page.emails.forEach { it.accountEmail = account }
                for (email in page.emails) {
                    totalFetched++
                    val senderKey = extractSenderPattern(email.sender)
                    val stats = breakdown.getOrPut(senderKey) { SenderStats() }
                    stats.fetched++

                    if (db.processedEmailDao().isProcessed(email.gmailId)) {
                        alreadyProcessed++
                        stats.skipped++
                        continue
                    }

                    try {
                        val body = GmailClient.fetchEmailBody(token, email.gmailId)
                        val result = LlmClient.extractTodosWithSummary(
                            apiKey, provider, email.sender, email.subject, body
                        )
                        val summary = "From: ${email.sender}\nSubject: ${email.subject}\n\n${result.summary}\n\n--- Full Email ---\n${body.take(5000)}"
                        for (extracted in result.todos) {
                            val eventAt = ReminderDefaults.parseEventTime(extracted.eventTime)
                            val todo = TodoItem(
                                text = extracted.text,
                                eventAt = eventAt,
                                reminderAt = eventAt?.let { ReminderDefaults.defaultNotificationTime(it) },
                                alarmAt = eventAt?.let { ReminderDefaults.defaultAlarmTime(it) },
                                reminderType = if (eventAt != null) "both" else null,
                                sourceGmailId = email.gmailId,
                                sourceRfc822Id = email.rfc822MsgId,
                                sourceEmailSummary = summary,
                                sourceAccount = email.accountEmail
                            )
                            db.todoDao().insert(todo)
                            if (eventAt != null) AlarmScheduler.schedule(context, todo)
                        }
                        db.processedEmailDao().insert(ProcessedEmail(gmailId = email.gmailId))
                        newProcessed++
                        stats.processed++
                        todosExtracted += result.todos.size
                        stats.todos += result.todos.size
                    } catch (_: Exception) {
                        errors++
                        kotlinx.coroutines.delay(2000)
                    }
                }
                pageToken = page.nextPageToken
            } while (pageToken != null)

            // Auto-discover new senders for this account
            todosExtracted += autoDiscoverNewSenders(context, token, account, apiKey, provider, db, lookback)
        }

        Prefs.setLastSyncTime(context, System.currentTimeMillis())
        return SyncReport(query, totalFetched, alreadyProcessed, newProcessed, todosExtracted, errors, breakdown, authError = authError)
    }

    private suspend fun autoDiscoverNewSenders(
        context: Context, token: String, account: String, apiKey: String, provider: String,
        db: TodoDatabase, lookback: String
    ): Int {
        val tracked = db.allowedSenderDao().getAllSync().map { it.pattern }
        val ignored = db.ignoredSenderDao().getAllPatterns()
        val knownPatterns = tracked + ignored

        val page = GmailClient.fetchEmails(token, query = lookback, maxResults = 20)
        page.emails.forEach { it.accountEmail = account }
        val newSenderEmails = mutableMapOf<String, GmailClient.EmailInfo>()

        for (email in page.emails) {
            val addr = extractSenderPattern(email.sender)
            if (matchesSenderList(email.sender, knownPatterns)) continue
            if (db.processedEmailDao().isProcessed(email.gmailId)) continue
            newSenderEmails.putIfAbsent(addr, email)
        }

        if (newSenderEmails.isEmpty()) return 0

        val representatives = newSenderEmails.values.toList()
        var pendingCount = 0

        try {
            val triples = representatives.map { Triple(it.sender, it.subject, it.snippet) }
            val results = LlmClient.classifyEmailsBatch(apiKey, provider, triples)

            for ((i, isImportant) in results.withIndex()) {
                val email = representatives[i]
                val addr = extractSenderPattern(email.sender)
                if (isImportant) {
                    try {
                        val body = GmailClient.fetchEmailBody(token, email.gmailId)
                        val result = LlmClient.extractTodosWithSummary(apiKey, provider, email.sender, email.subject, body)
                        if (result.todos.isNotEmpty()) {
                            val todoPreview = result.todos.joinToString(", ") { it.text }
                            db.pendingSenderDao().insert(PendingSender(
                                pattern = addr,
                                displayName = email.sender,
                                sampleSubject = email.subject,
                                sampleTodos = todoPreview,
                                sampleGmailId = email.gmailId
                            ))
                            pendingCount++
                        }
                    } catch (_: Exception) { }
                } else {
                    db.ignoredSenderDao().insert(IgnoredSender(pattern = addr))
                }
                db.processedEmailDao().insert(ProcessedEmail(gmailId = email.gmailId))
            }
        } catch (_: Exception) { }

        if (pendingCount > 0) {
            NotificationHelper.showNotification(
                context,
                "New senders detected",
                "$pendingCount new sender(s) with action items — tap to review"
            )
        }
        return 0
    }

    private fun getLookbackQuery(context: Context): String {
        val lastSync = Prefs.getLastSyncTime(context)
        if (lastSync > 0) {
            val epochSecs = lastSync / 1000
            return "after:$epochSecs"
        }
        return "newer_than:7d"
    }

    private fun buildGmailQuery(patterns: List<String>, lookback: String): String {
        val fromClauses = patterns.map { pattern ->
            if (pattern.startsWith("*@")) {
                "from:@${pattern.removePrefix("*@")}"
            } else {
                "from:${pattern.removePrefix("*")}"
            }
        }
        return "(${fromClauses.joinToString(" OR ")}) $lookback"
    }

    fun extractSenderPattern(sender: String): String {
        val email = Regex("<([^>]+)>").find(sender)?.groupValues?.get(1)
            ?: sender.trim()
        return email.lowercase()
    }

    fun matchesSenderList(sender: String, patterns: List<String>): Boolean {
        val senderLower = sender.lowercase()
        val emailOnly = Regex("<([^>]+)>").find(senderLower)
            ?.groupValues?.get(1) ?: senderLower

        return patterns.any { pattern ->
            val p = pattern.lowercase()
            when {
                p.startsWith("*@") -> emailOnly.endsWith(p.removePrefix("*"))
                p.startsWith("*") -> emailOnly.contains(p.removePrefix("*"))
                else -> emailOnly == p || senderLower.contains(p)
            }
        }
    }
}
