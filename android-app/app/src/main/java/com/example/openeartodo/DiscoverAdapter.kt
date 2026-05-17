package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

data class DiscoverGroup(
    val senderPattern: String,
    val senderDisplay: String,
    val emailCount: Int,
    val todos: List<String>,
    val emailDetails: List<DiscoverEmail>
)

data class DiscoverEmail(
    val gmailId: String,
    val rfc822MsgId: String?,
    val subject: String,
    val sender: String,
    val todos: List<LlmClient.ExtractedTodo>,
    val summary: String,
    val body: String
)

class DiscoverAdapter(
    private val items: MutableList<DiscoverGroup> = mutableListOf(),
    private val selectedSenders: MutableSet<String> = mutableSetOf()
) : RecyclerView.Adapter<DiscoverAdapter.ViewHolder>() {

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val cbSelect: CheckBox = itemView.findViewById(R.id.cbSelect)
        val tvSender: TextView = itemView.findViewById(R.id.tvSender)
        val tvStats: TextView = itemView.findViewById(R.id.tvStats)
        val tvTodos: TextView = itemView.findViewById(R.id.tvTodos)
    }

    fun setItems(newItems: List<DiscoverGroup>) {
        items.clear()
        items.addAll(newItems)
        selectedSenders.clear()
        notifyDataSetChanged()
    }

    fun getSelectedGroups(): List<DiscoverGroup> =
        items.filter { it.senderPattern in selectedSenders }

    fun getUnselectedPatterns(): List<String> =
        items.filter { it.senderPattern !in selectedSenders }.map { it.senderPattern }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_discover, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val group = items[position]

        holder.tvSender.text = group.senderDisplay
        holder.tvStats.text = "${group.emailCount} email(s), ${group.todos.size} action item(s)"
        holder.tvTodos.text = group.todos.joinToString("\n") { "• $it" }

        holder.cbSelect.setOnCheckedChangeListener(null)
        holder.cbSelect.isChecked = group.senderPattern in selectedSenders
        holder.cbSelect.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) selectedSenders.add(group.senderPattern) else selectedSenders.remove(group.senderPattern)
        }

        holder.itemView.setOnClickListener {
            holder.cbSelect.isChecked = !holder.cbSelect.isChecked
        }
    }

    override fun getItemCount() = items.size
}
