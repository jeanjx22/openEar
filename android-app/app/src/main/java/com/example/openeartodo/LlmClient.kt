package com.example.openeartodo

import com.google.gson.Gson
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.POST
import java.util.concurrent.TimeUnit

object LlmClient {

    data class ProviderConfig(val baseUrl: String, val model: String)

    val providers = mapOf(
        "cohere" to ProviderConfig("https://api.cohere.com/compatibility/v1/", "command-r-08-2024"),
        "groq" to ProviderConfig("https://api.groq.com/openai/v1/", "llama-3.3-70b-versatile")
    )

    // OpenAI-compatible chat API
    interface ChatApi {
        @POST("chat/completions")
        suspend fun complete(
            @Header("Authorization") auth: String,
            @Body request: ChatRequest
        ): ChatResponse
    }

    data class ChatRequest(
        val model: String,
        val messages: List<Msg>,
        val temperature: Double = 0.1
    )
    data class Msg(val role: String, val content: String)
    data class ChatResponse(val choices: List<Choice>)
    data class Choice(val message: Msg)

    private val httpClient = OkHttpClient.Builder()
        .readTimeout(60, TimeUnit.SECONDS)
        .connectTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun buildApi(baseUrl: String): ChatApi =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(httpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ChatApi::class.java)

    suspend fun extractTodos(
        apiKey: String,
        provider: String,
        sender: String,
        subject: String,
        body: String
    ): List<String> {
        val config = providers[provider] ?: providers["cohere"]!!
        val api = buildApi(config.baseUrl)

        val systemPrompt = """Extract actionable TODO items from this email. Return ONLY a JSON array of short, actionable task strings.

Rules:
- Each item should be a concrete action the recipient needs to take
- Keep items short (under 80 chars)
- Include deadlines in the task text if mentioned
- Skip informational content that requires no action
- If there are no actionable items, return an empty array []

Examples:
- ["Sign permission slip by Friday", "Send lunch money via Venmo", "RSVP for conference Oct 12"]
- ["Schedule dentist follow-up", "Pick up prescription at CVS"]
- []"""

        val request = ChatRequest(
            model = config.model,
            messages = listOf(
                Msg("system", systemPrompt),
                Msg("user", "From: $sender\nSubject: $subject\n\n${body.take(3000)}")
            )
        )

        val response = api.complete("Bearer $apiKey", request)
        val content = response.choices.firstOrNull()?.message?.content ?: return emptyList()

        return try {
            val cleaned = content.trim()
                .removePrefix("```json").removePrefix("```")
                .removeSuffix("```").trim()
            Gson().fromJson(cleaned, Array<String>::class.java).toList()
        } catch (e: Exception) {
            emptyList()
        }
    }
}
