"""Message formatters for Telegram display.

All datetime values stored in the database are UTC. These formatters
convert to the user's local timezone for display purposes only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo


def to_local(dt: datetime, tz_name: str = "America/Los_Angeles") -> str:
    """Convert UTC datetime to local time string for display."""
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return local.strftime("%b %d, %I:%M %p")


def format_briefing(emails: list[dict], tz_name: str = "America/Los_Angeles") -> str:
    """Format an email briefing message."""
    if not emails:
        return "No important emails right now."

    lines = [f"You have {len(emails)} important email(s):\n"]
    for i, email in enumerate(emails, 1):
        label = email.get("label", "")
        label_tag = f" [{label}]" if label else ""
        received = email.get("received_at")
        time_str = ""
        if received:
            time_str = f" ({to_local(received, tz_name)})"

        lines.append(f"{i}. {email['subject']}{label_tag}{time_str}")
        lines.append(f"   From: {email['sender']}")
        if email.get("summary"):
            lines.append(f"   {email['summary']}")
        lines.append("")

    return "\n".join(lines)


def format_reminder(reminder, tz_name: str = "America/Los_Angeles") -> str:
    """Format a single reminder for display."""
    due_str = to_local(reminder.due_at, tz_name)
    recurrence = f" (repeats {reminder.recurrence})" if reminder.recurrence else ""
    desc = f"\n{reminder.description}" if reminder.description else ""
    return f"Reminder: {reminder.title}\nDue: {due_str}{recurrence}{desc}"


def format_reminder_list(
    reminders: list, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format a list of reminders."""
    if not reminders:
        return "No active reminders."

    lines = [f"Active reminders ({len(reminders)}):\n"]
    for r in reminders:
        due_str = to_local(r.due_at, tz_name)
        status_emoji = {"active": "", "snoozed": " (snoozed)", "completed": " (done)"}
        lines.append(
            f"  #{r.id}: {r.title} - {due_str}{status_emoji.get(r.status, '')}"
        )
    return "\n".join(lines)


def to_local_full(dt: datetime, tz_name: str = "America/Los_Angeles") -> str:
    """Convert UTC datetime to a full local time string with day name."""
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return local.strftime("%A %b %d, %I:%M %p")


def format_pre_alert(alert_reminder, parent_reminder, tz_name: str = "America/Los_Angeles") -> str:
    """Format a pre-alert notification showing the parent event details.

    Instead of showing the alert's own due time (which is confusing),
    this shows the parent event's time and the alert label.
    """
    parent_due_str = to_local_full(parent_reminder.due_at, tz_name)
    title = parent_reminder.title or "Reminder"
    alert_label = alert_reminder.alert_label or "Upcoming"
    return (
        f"⏰ Alert: {title}\n"
        f"📅 Event: {parent_due_str}\n"
        f"🔔 This is your {alert_label} alert\n"
        f"🐰"
    )


def format_reminder_card(reminder, alerts: list, tz_name: str = "America/Los_Angeles") -> str:
    """Format a single reminder card for the /reminders list.

    Shows the title, due date/time in local timezone, and a summary
    of associated alerts (pre-alerts) with their times.
    """
    tz = ZoneInfo(tz_name)
    due_dt = reminder.due_at
    if due_dt.tzinfo is None:
        due_dt = due_dt.replace(tzinfo=timezone.utc)
    local_due = due_dt.astimezone(tz)
    due_str = local_due.strftime("%a %b %d, %-I:%M %p")

    lines = []
    lines.append(f"\U0001f5d3 {reminder.title}")
    lines.append(f"\U0001f4c5 {due_str}")

    if alerts:
        alert_parts = []
        for a in alerts:
            label = a.alert_label or "Alert"
            a_due = a.due_at
            if a_due.tzinfo is None:
                a_due = a_due.replace(tzinfo=timezone.utc)
            a_local = a_due.astimezone(tz)
            day_name = a_local.strftime("%a")
            time_str = a_local.strftime("%-I%p").lower()
            alert_parts.append(f"{label} ({day_name} {time_str})")
        lines.append(f"\U0001f514 {' \u00b7 '.join(alert_parts)}")
    else:
        lines.append("\U0001f514 No alerts set")

    return "\n".join(lines)


def format_note(note, tz_name: str = "America/Los_Angeles") -> str:
    """Format a single note for display."""
    created = to_local(note.created_at, tz_name)
    tags = ""
    if note.tags and note.tags != "[]":
        import json

        try:
            tag_list = json.loads(note.tags)
            if tag_list:
                tags = f"\nTags: {', '.join(tag_list)}"
        except Exception:
            pass
    return f"Note #{note.id} ({created}):\n{note.content}{tags}"


def format_note_list(
    notes: list, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format a list of notes."""
    if not notes:
        return "No notes saved."

    lines = [f"Notes ({len(notes)}):\n"]
    for n in notes:
        created = to_local(n.created_at, tz_name)
        preview = n.content[:60] + ("..." if len(n.content) > 60 else "")
        lines.append(f"  #{n.id} ({created}): {preview}")
    return "\n".join(lines)


def format_note_search_results(
    notes: list, query: str, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format note search results."""
    if not notes:
        return f"🔍 Nothing found for '{query}' 🐰"

    lines = [f"📝 Found {len(notes)} note(s) for '{query}':\n"]
    for n in notes:
        created = to_local(n.created_at, tz_name)
        tags = ""
        if n.tags and n.tags != "[]":
            import json
            try:
                tag_list = json.loads(n.tags)
                tag_list = [t for t in tag_list if t != "activity_log"]
                if tag_list:
                    tags = f"  🏷 {', '.join(tag_list)}"
            except Exception:
                pass
        lines.append(f"  💬 {n.content}")
        lines.append(f"     📅 {created}{tags}")
        lines.append("")
    lines.append("🐰")
    return "\n".join(lines)


def format_activity_log(
    activities: list, who: str, tz_name: str = "America/Los_Angeles"
) -> str:
    """Format activity log entries chronologically."""
    if not activities:
        return f"🤷 No activities logged for {who or 'anyone'} 🐰"

    sorted_activities = sorted(activities, key=lambda a: a.created_at)
    name = who.title() if who else "Family"
    lines = [f"🏃 {name}'s activities:\n"]
    current_day = None
    tz = ZoneInfo(tz_name)
    for a in sorted_activities:
        dt = a.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        day_str = local_dt.strftime("%A %b %d")
        time_str = local_dt.strftime("%-I:%M %p")
        if day_str != current_day:
            current_day = day_str
            lines.append(f"\n📅 {day_str}:")
        content = a.content
        emoji = "•"
        cl = content.lower()
        if "tennis" in cl:
            emoji = "🎾"
        elif "gym" in cl or "workout" in cl or "exercise" in cl:
            emoji = "💪"
        elif "swim" in cl:
            emoji = "🏊"
        elif "run" in cl or "jog" in cl:
            emoji = "🏃"
        elif "doctor" in cl or "dentist" in cl or "hospital" in cl:
            emoji = "🏥"
        elif "school" in cl or "class" in cl:
            emoji = "📚"
        elif "lunch" in cl or "dinner" in cl or "food" in cl:
            emoji = "🍽"
        lines.append(f"  {emoji} {content}")
    lines.append("\n🐰")
    return "\n".join(lines)


def format_stock_briefing(quotes: list[str | BaseException]) -> str:
    """Format stock quotes for the morning briefing.

    Args:
        quotes: Results from asyncio.gather(return_exceptions=True).
            Each element is either a formatted quote string or an
            exception (timeout / fetch failure).

    Returns:
        A section string to append to the email briefing.
    """
    lines = ["\n---\nMarket snapshot:\n"]
    for q in quotes:
        if isinstance(q, BaseException):
            lines.append(f"  -- (unavailable: {q})")
        else:
            # get_stock_quote returns multi-line rich output for
            # successful lookups and single-line for fallback/error.
            # Indent every line for consistent briefing formatting.
            for part in str(q).splitlines():
                if part.strip():
                    lines.append(f"  {part}")
            lines.append("")
    return "\n".join(lines)


def format_upcoming_events(
    reminders: list,
    tz_name: str = "America/Los_Angeles",
    is_sunday: bool = False,
) -> str:
    """Format upcoming reminders (next 7 days) for the evening briefing.

    On Sunday evenings a richer "Week Ahead Preview" header is shown
    and events are grouped by day.

    Args:
        reminders: Reminder objects filtered to the next 7 days.
        tz_name: IANA timezone name for display.
        is_sunday: True when the briefing runs on Sunday evening.

    Returns:
        A section string to append to the email briefing,
        or an empty string if there are no upcoming events.
    """
    if not reminders:
        return "\n---\nNo upcoming events this week."

    tz = ZoneInfo(tz_name)

    if is_sunday:
        lines = ["\n---\nWeek Ahead Preview:\n"]
        # Group reminders by local day
        by_day: dict[str, list] = defaultdict(list)
        for r in reminders:
            dt = r.due_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(tz)
            day_key = local_dt.strftime("%A %b %d")
            time_str = local_dt.strftime("%-I:%M %p")
            by_day[day_key].append(f"{r.title} at {time_str}")

        for day, items in by_day.items():
            lines.append(f"  {day}:")
            for item in items:
                lines.append(f"    - {item}")
        return "\n".join(lines)

    # Regular evening: flat list
    lines = ["\n---\nUpcoming this week:\n"]
    for r in reminders:
        due_str = to_local_full(r.due_at, tz_name)
        lines.append(f"  - {r.title} -- {due_str}")
    return "\n".join(lines)
