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
                    "Reschedule",
                    callback_data=f"post_reschedule:{reminder_id}",
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


def pre_alert_actions(reminder_id: int, parent_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for pre-alert notifications.

    Includes Done, Snooze options, and a 'Set more alerts' button
    that lets the user add additional alerts for the parent event.
    """
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
                    "Set more alerts",
                    callback_data=f"alert_more:{parent_id}",
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


def note_remind_with_context(note_id: int, event_desc: str) -> InlineKeyboardMarkup:
    """Inline keyboard offering to set a reminder for a note with a future event."""
    # Truncate event description for button text (Telegram limit is 64 bytes)
    label = f"Set reminder for {event_desc[:30]}?" if event_desc else "Set a reminder?"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"note_remind:{note_id}",
                ),
            ]
        ]
    )


def manage_alerts(reminder_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for managing alerts on an existing reminder."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Add alert",
                    callback_data=f"add_alert:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Delete alerts",
                    callback_data=f"delete_alerts:{reminder_id}",
                ),
            ]
        ]
    )


def post_event_actions(reminder_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for post-event follow-up prompts."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, mark done",
                    callback_data=f"post_done:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Snooze 1hr",
                    callback_data=f"post_snooze:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Reschedule",
                    callback_data=f"post_reschedule:{reminder_id}",
                ),
            ],
        ]
    )


def delete_alert_actions(alerts: list, tz_name: str = "America/Los_Angeles") -> InlineKeyboardMarkup:
    """Inline keyboard listing individual alerts with times for deletion."""
    from src.bot.formatters import to_local

    rows = []
    for alert in alerts:
        alert_label = alert.alert_label or "Alert"
        time_str = to_local(alert.due_at, tz_name) if alert.due_at else "?"
        label = f"{alert_label} — {time_str}"
        rows.append(
            [
                InlineKeyboardButton(
                    f"🗑 {label}",
                    callback_data=f"del_alert:{alert.id}",
                ),
            ]
        )
    rows.append(
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    )
    return InlineKeyboardMarkup(rows)


def disambiguate_alert_or_reminder(reminder_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown when parse_alert_time fails in AWAITING_ALERT_TIME state.

    Lets the user explicitly choose: exit to create a new reminder,
    or stay and retry the alert time input.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, new reminder",
                    callback_data=f"disambig_new_reminder:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "No, try again",
                    callback_data=f"disambig_retry_alert:{reminder_id}",
                ),
            ]
        ]
    )


def reminder_list_actions(reminder_id: int, has_alerts: bool) -> InlineKeyboardMarkup:
    """Inline keyboard for each reminder card in the /reminders list.

    Shows 'Manage alerts' if the reminder already has alerts,
    or 'Add alerts' if it has none. Always shows Done and Reschedule.
    """
    if has_alerts:
        alert_btn = InlineKeyboardButton(
            "Manage alerts",
            callback_data=f"manage_alerts:{reminder_id}",
        )
    else:
        alert_btn = InlineKeyboardButton(
            "Add alerts",
            callback_data=f"add_alert:{reminder_id}",
        )
    return InlineKeyboardMarkup(
        [
            [
                alert_btn,
                InlineKeyboardButton(
                    "Done",
                    callback_data=f"reminder_done:{reminder_id}",
                ),
                InlineKeyboardButton(
                    "Reschedule",
                    callback_data=f"post_reschedule:{reminder_id}",
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


def confirm_settings(settings_id: str) -> InlineKeyboardMarkup:
    """Confirm/Cancel keyboard for setup settings changes.

    Args:
        settings_id: unique identifier for the pending settings batch,
            stored in bot_data so the callback can retrieve and apply them.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm",
                    callback_data=f"setup_confirm:{settings_id}",
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"setup_cancel:{settings_id}",
                ),
            ]
        ]
    )
