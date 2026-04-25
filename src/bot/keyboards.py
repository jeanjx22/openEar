"""Inline keyboard builders for Telegram bot interactions.

Provides keyboard markup for:
- Email briefing action items (Remind Me, Already Done, Dismiss)
- Reminder actions (Done, Snooze 1hr, Snooze tomorrow, Repeat weekly)
- Note follow-ups (Set reminder?, Search related)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def briefing_actions(email_index: int) -> InlineKeyboardMarkup:
    """Inline keyboard for email briefing action items."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Remind Me",
                    callback_data=f"email_remind:{email_index}",
                ),
                InlineKeyboardButton(
                    "Already Done",
                    callback_data=f"email_done:{email_index}",
                ),
                InlineKeyboardButton(
                    "Dismiss",
                    callback_data=f"email_dismiss:{email_index}",
                ),
            ]
        ]
    )


def reminder_actions(reminder_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for reminder notifications."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Done", callback_data=f"reminder_done:{reminder_id}"
                ),
                InlineKeyboardButton(
                    "Snooze 1hr",
                    callback_data=f"reminder_snooze_1h:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Snooze tomorrow",
                    callback_data=f"reminder_snooze_tomorrow:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Repeat weekly",
                    callback_data=f"reminder_repeat_weekly:{reminder_id}",
                ),
            ],
        ]
    )


def alert_preferences(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Day before + morning",
                    callback_data=f"alert_daymorning:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Morning of only",
                    callback_data=f"alert_morning:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "At the time only",
                    callback_data=f"alert_none:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "1 hour before",
                    callback_data=f"alert_1h:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Custom",
                    callback_data=f"alert_custom:{reminder_id}",
                ),
            ],
        ]
    )


def note_followup(note_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for note follow-up suggestions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Set reminder?",
                    callback_data=f"note_remind:{note_id}",
                ),
            ]
        ]
    )


def confirm_cancel() -> InlineKeyboardMarkup:
    """Generic confirm/cancel keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data="confirm"),
                InlineKeyboardButton("Cancel", callback_data="cancel"),
            ]
        ]
    )
