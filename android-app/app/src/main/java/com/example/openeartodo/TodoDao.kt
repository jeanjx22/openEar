package com.example.openeartodo

import androidx.lifecycle.LiveData
import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update

@Dao
interface TodoDao {
    @Insert
    suspend fun insert(todo: TodoItem)
    
    @Update
    suspend fun update(todo: TodoItem)
    
    @Delete
    suspend fun delete(todo: TodoItem)
    
    @Query("SELECT * FROM todoitem ORDER BY createdAt DESC")
    fun getAllTodos(): LiveData<List<TodoItem>>
}