package com.example.openeartodo

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [TodoItem::class, AllowedSender::class, ProcessedEmail::class, IgnoredSender::class],
    version = 11,
    exportSchema = false
)
abstract class TodoDatabase : RoomDatabase() {
    abstract fun todoDao(): TodoDao
    abstract fun allowedSenderDao(): AllowedSenderDao
    abstract fun processedEmailDao(): ProcessedEmailDao
    abstract fun ignoredSenderDao(): IgnoredSenderDao

    companion object {
        @Volatile
        private var INSTANCE: TodoDatabase? = null

        private val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""CREATE TABLE IF NOT EXISTS `ignored_sender` (
                    `pattern` TEXT NOT NULL,
                    `createdAt` INTEGER NOT NULL,
                    PRIMARY KEY(`pattern`)
                )""")
            }
        }

        private val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `reminderAt` INTEGER")
            }
        }

        private val MIGRATION_9_10 = object : Migration(9, 10) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `reminderType` TEXT")
            }
        }

        private val MIGRATION_10_11 = object : Migration(10, 11) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `alarmAt` INTEGER")
            }
        }

        private val MIGRATION_CREATE_ALL = object : Migration(1, 11) {
            override fun migrate(db: SupportSQLiteDatabase) {
                createAllTables(db)
            }
        }

        private fun createAllTables(db: SupportSQLiteDatabase) {
            db.execSQL("""CREATE TABLE IF NOT EXISTS `todoitem` (
                `text` TEXT NOT NULL,
                `createdAt` INTEGER NOT NULL,
                `isCompleted` INTEGER NOT NULL DEFAULT 0,
                `completedAt` INTEGER,
                `reminderAt` INTEGER,
                `reminderType` TEXT,
                `alarmAt` INTEGER,
                `sourceGmailId` TEXT,
                `sourceRfc822Id` TEXT,
                `sourceEmailSummary` TEXT,
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL
            )""")
            db.execSQL("""CREATE TABLE IF NOT EXISTS `allowed_sender` (
                `pattern` TEXT NOT NULL,
                `label` TEXT NOT NULL,
                `createdAt` INTEGER NOT NULL,
                PRIMARY KEY(`pattern`)
            )""")
            db.execSQL("""CREATE TABLE IF NOT EXISTS `processed_email` (
                `gmailId` TEXT NOT NULL,
                `processedAt` INTEGER NOT NULL,
                PRIMARY KEY(`gmailId`)
            )""")
            db.execSQL("""CREATE TABLE IF NOT EXISTS `ignored_sender` (
                `pattern` TEXT NOT NULL,
                `createdAt` INTEGER NOT NULL,
                PRIMARY KEY(`pattern`)
            )""")
        }

        fun getInstance(context: Context): TodoDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    TodoDatabase::class.java,
                    "todo_database"
                )
                    .addMigrations(MIGRATION_7_8, MIGRATION_8_9, MIGRATION_9_10, MIGRATION_10_11)
                    .fallbackToDestructiveMigrationFrom(1, 2, 3, 4, 5, 6)
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
