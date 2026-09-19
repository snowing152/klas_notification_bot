from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.orm import declarative_base

# Create base class for declarative models
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True)
    username = Column(String, nullable=False)
    encrypted_password = Column(String, nullable=False)
    language = Column(String, nullable=False, default="en")

    def to_dict(self):
        return {
            "username": self.username,
            "password": self.encrypted_password,
            "language": self.language,
        }


class LibraryUser(Base):
    __tablename__ = "library_users"

    user_id = Column(String, primary_key=True)
    username = Column(String, nullable=False)
    encrypted_password = Column(String, nullable=False)
    phone_number = Column(String, nullable=False)

    def to_dict(self):
        return {
            "username": self.username,
            "password": self.encrypted_password,
            "phone_number": self.phone_number,
        }


class SentNotification(Base):
    """One row per notification already delivered, so a restart cannot repeat it.

    The tracker used to live in a dict in the notification service, which meant
    every deploy re-sent whatever thresholds were current at the time. `kind` is
    either "new" (the assignment was announced when it appeared) or "t<hours>"
    for a deadline threshold.
    """

    __tablename__ = "sent_notifications"

    user_id = Column(String, primary_key=True)
    # subject + type + title; KLAS gives assignments no stable id of their own
    assignment_key = Column(String, primary_key=True)
    kind = Column(String, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())


class NotificationState(Base):
    """Per-user bookkeeping for the notification loop."""

    __tablename__ = "notification_state"

    user_id = Column(String, primary_key=True)
    # False until one cycle has recorded the assignments the student already
    # had, so switching the feature on does not announce all of them as new.
    seeded = Column(Boolean, nullable=False, default=False)
    # Consecutive failed KLAS logins. KW forces a password change every few
    # months; without this the bot just goes quiet and the student never learns
    # why their notifications stopped.
    login_failures = Column(Integer, nullable=False, default=0)
    credentials_warned = Column(Boolean, nullable=False, default=False)


class UserSettings(Base):
    """Per-user notification preferences, defaults chosen to match old behaviour."""

    __tablename__ = "user_settings"

    user_id = Column(String, primary_key=True)
    new_assignment_alerts = Column(Boolean, nullable=False, default=True)
    deadline_alerts = Column(Boolean, nullable=False, default=True)
    # Only the 6/3/1 hour thresholds, for students who found six messages per
    # assignment too much.
    urgent_thresholds_only = Column(Boolean, nullable=False, default=False)
    # KLAS deadlines are usually 23:59, so the 24h and 12h warnings land at
    # night. Held notifications go out on the first cycle after quiet_end.
    quiet_hours = Column(Boolean, nullable=False, default=True)
    quiet_start = Column(Integer, nullable=False, default=23)
    quiet_end = Column(Integer, nullable=False, default=8)


class AnnouncementSeen(Base):
    """Which feature announcements a user has already been shown.

    Keyed rather than dated: a redeploy must not re-announce a feature, and a
    new announcement must reach users who joined before it existed.
    """

    __tablename__ = "announcements_seen"

    user_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)
