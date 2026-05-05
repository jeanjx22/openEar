package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class TodoAdapter(private val items: MutableList<TodoItem>) :
    RecyclerView.Adapter<TodoAdapter.ViewHolder>() {

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val cbComplete: CheckBox = itemView.findViewById(R.id.cbComplete)
        val tvText: TextView = itemView.findViewById(R.id.tvTodoText)
    }

    fun setItems(newItems: List<TodoItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return ViewHolder(inflater.inflate(R.layout.item_todo, parent, false))
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.tvText.text = item.text
        holder.cbComplete.isChecked = item.isCompleted
        
        holder.cbComplete.setOnCheckedChangeListener { _, isChecked ->
            item.isCompleted = isChecked
            // Add update logic here
        }
    }

    override fun getItemCount() = items.size
}