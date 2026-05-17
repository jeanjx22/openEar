package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class PendingAdapter(
    private val onTrack: (PendingSender) -> Unit,
    private val onIgnore: (PendingSender) -> Unit
) : RecyclerView.Adapter<PendingAdapter.ViewHolder>() {

    private val items = mutableListOf<PendingSender>()

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val tvSender: TextView = itemView.findViewById(R.id.tvSender)
        val tvSubject: TextView = itemView.findViewById(R.id.tvSubject)
        val tvTodos: TextView = itemView.findViewById(R.id.tvTodos)
        val btnTrack: Button = itemView.findViewById(R.id.btnTrack)
        val btnIgnore: Button = itemView.findViewById(R.id.btnIgnore)
    }

    fun setItems(newItems: List<PendingSender>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_pending, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val sender = items[position]
        holder.tvSender.text = sender.displayName
            .substringBefore('<').trim()
            .ifEmpty { sender.pattern }
        holder.tvSubject.text = "Subject: ${sender.sampleSubject}"
        holder.tvTodos.text = sender.sampleTodos.split(", ").joinToString("\n") { "• $it" }
        holder.btnTrack.setOnClickListener { onTrack(sender) }
        holder.btnIgnore.setOnClickListener { onIgnore(sender) }
    }

    override fun getItemCount() = items.size
}
