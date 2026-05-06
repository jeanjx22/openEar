package com.example.openeartodo

import android.content.Context

object EmailProcessor {

    suspend fun processNewEmails(context: Context): Int {
        val account = Prefs.getGmailAccount(context) ?: return 0
        val apiKey = Prefs.getLlmApiKey(context)
        if (apiKey.isBlank()) return 0

        val provider = Prefs.getLlmProvider(context)
        val token = GmailClient.getAccessToken(context, account)
        val emails = GmailClient.fetchRecentEmails(token)

        val db = TodoDatabase.getInstance(context)
        val patterns = db.allowedSenderDao().getAllSync().map { it.pattern.lowercase() }
        if (patterns.isEmpty()) return 0

        var count = 0
        for (email in emails) {
            if (db.processedEmailDao().isProcessed(email.gmailId)) continue
            if (!matchesAny(email.sender, patterns)) continue

            val body = GmailClient.fetchEmailBody(token, email.gmailId)
            val todos = LlmClient.extractTodos(apiKey, provider, email.sender, email.subject, body)

            for (text in todos) {
                db.todoDao().insert(TodoItem(text = text, sourceGmailId = email.gmailId))
            }
            db.processedEmailDao().insert(ProcessedEmail(gmailId = email.gmailId))
            count += todos.size
        }

        return count
    }

    private fun matchesAny(sender: String, patterns: List<String>): Boolean {
        val senderLower = sender.lowercase()
        val emailOnly = Regex("<([^>]+)>").find(senderLower)
            ?.groupValues?.get(1) ?: senderLower

        return patterns.any { pattern ->
            val regex = Regex(
                pattern.replace(".", "\\.").replace("*", ".*"),
                RegexOption.IGNORE_CASE
            )
            regex.containsMatchIn(emailOnly) || regex.containsMatchIn(senderLower)
        }
    }

    fun extractSenderDomain(sender: String): String {
        val email = Regex("<([^>]+)>").find(sender)?.groupValues?.get(1)
            ?: sender.trim()
        val domain = email.substringAfter('@')
        return "*@$domain"
    }
}
