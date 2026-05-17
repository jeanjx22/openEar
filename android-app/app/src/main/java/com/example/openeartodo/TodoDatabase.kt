package com.example.openeartodo

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [TodoItem::class, AllowedSender::class, ProcessedEmail::class, IgnoredSender::class, PendingSender::class],
    version = 14,
    exportSchema = false
)
abstract class TodoDatabase : RoomDatabase() {
    abstract fun todoDao(): TodoDao
    abstract fun allowedSenderDao(): AllowedSenderDao
    abstract fun processedEmailDao(): ProcessedEmailDao
    abstract fun ignoredSenderDao(): IgnoredSenderDao
    abstract fun pendingSenderDao(): PendingSenderDao

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

        private val MIGRATION_11_12 = object : Migration(11, 12) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `eventAt` INTEGER")
            }
        }

        private val MIGRATION_12_13 = object : Migration(12, 13) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `recurrence` TEXT")
                db.execSQL("ALTER TABLE `todoitem` ADD COLUMN `snoozedUntil` INTEGER")
            }
        }

        private val MIGRATION_13_14 = object : Migration(13, 14) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""CREATE TABLE IF NOT EXISTS `pending_sender` (
                    `pattern` TEXT NOT NULL,
                    `displayName` TEXT NOT NULL,
                    `sampleSubject` TEXT NOT NULL,
                    `sampleTodos` TEXT NOT NULL,
                    `sampleGmailId` TEXT,
                    `createdAt` INTEGER NOT NULL,
                    PRIMARY KEY(`pattern`)
                )""")
            }
        }

        fun getInstance(context: Context): TodoDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    TodoDatabase::class.java,
                    "todo_database"
                )
                    .addMigrations(MIGRATION_7_8, MIGRATION_8_9, MIGRATION_9_10, MIGRATION_10_11, MIGRATION_11_12, MIGRATION_12_13, MIGRATION_13_14)
                    .fallbackToDestructiveMigrationFrom(1, 2, 3, 4, 5, 6)
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
