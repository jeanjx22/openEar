package com.example.openeartodo

import android.content.Context

data class SyncReport(
    val query: String,
    val totalFetched: Int,
    val alreadyProcessed: Int,
    val newProcessed: Int,
    val todosExtracted: Int,
    val errors: Int,
    val senderBreakdown: Map<String, SenderStats>
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
        val account = Prefs.getGmailAccount(context) ?: return emptyReport
        val apiKey = Prefs.getLlmApiKey(context)
        if (apiKey.isBlank()) return emptyReport

        val provider = Prefs.getLlmProvider(context)
        val db = TodoDatabase.getInstance(context)
        val senders = db.allowedSenderDao().getAllSync()
        if (senders.isEmpty()) return emptyReport

        val token = GmailClient.getAccessToken(context, account)

        val lookback = lookbackOverride ?: getLookbackQuery(context)
        val query = buildGmailQuery(senders.map { it.pattern }, lookback)
        var pageToken: String? = null
        val breakdown = mutableMapOf<String, SenderStats>()
        var totalFetched = 0
        var alreadyProcessed = 0
        var newProcessed = 0
        var todosExtracted = 0
        var errors = 0

        do {
            val page = GmailClient.fetchEmails(token, query = query, pageToken = pageToken)
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
                    for (text in result.todos) {
                        db.todoDao().insert(TodoItem(
                            text = text,
                            sourceGmailId = email.gmailId,
                            sourceRfc822Id = email.rfc822MsgId,
                            sourceEmailSummary = summary
                        ))
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

        Prefs.setLastSyncTime(context, System.currentTimeMillis())
        return SyncReport(query, totalFetched, alreadyProcessed, newProcessed, todosExtracted, errors, breakdown)
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
