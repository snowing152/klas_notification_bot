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
    # No longer written: the notification cycle does not log in any more (KLAS
    # wants push approval each time), so there is nothing to count. Kept because
    # dropping a column needs a migration this project does not have.
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
    # A night's sleep, not an evening. KLAS deadlines are usually 23:59, so the
    # 24h warning falls due at midnight: a window opening at 23:00 held it
    # until morning, when sixteen hours were left. Held notifications go out on
    # the first cycle after quiet_end.
    quiet_hours = Column(Boolean, nullable=False, default=True)
    quiet_start = Column(Integer, nullable=False, default=1)
    quiet_end = Column(Integer, nullable=False, default=8)


class KlasSession(Base):
    """The KLAS session the bot keeps for a user between operations.

    KLAS asks for push approval in its phone app on every login, so the bot
    cannot log in again on its own: it keeps one approved session alive and
    asks the student to approve again only once that session has died. All
    columns are here from the start because create_all cannot add a column to
    a table that already exists.
    """

    __tablename__ = "klas_sessions"

    user_id = Column(String, primary_key=True)
    # Fernet token of the JSON cookie dict; None once the session is dead.
    # A live session is as good as the password for as long as it lasts.
    encrypted_cookies = Column(String, nullable=True)
    needs_login = Column(Boolean, nullable=False, default=False)
    # Whether the user was already told to approve a new login, so the
    # notification loop says it once and not every cycle.
    login_prompt_sent = Column(Boolean, nullable=False, default=False)
    # KLAS rejected the saved password on a re-login. Further taps on the
    # login button would resend it, and KLAS locks the account after five
    # failures, so re-login stays refused until /register saves a new one.
    password_rejected = Column(Boolean, nullable=False, default=False)
    # Seoul time (app.utils.timezone); logged_in_at shows how long KLAS lets a
    # kept session live, which is not documented anywhere.
    logged_in_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class AnnouncementSeen(Base):
    """Which feature announcements a user has already been shown.

    Keyed rather than dated: a redeploy must not re-announce a feature, and a
    new announcement must reach users who joined before it existed.
    """

    __tablename__ = "announcements_seen"

    user_id = Column(String, primary_key=True)
    key = Column(String, primary_key=True)
