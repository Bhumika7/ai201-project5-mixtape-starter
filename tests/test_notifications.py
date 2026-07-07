"""
tests/test_notifications.py — Mixtape

Regression test for Issue #4: rating a friend's song should notify the
song's original sharer, the same way adding it to a playlist does.
"""
import pytest

from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_song(app):
    """A sharer who posted a song, and a separate rater."""
    with app.app_context():
        sharer = User(username="nova", email="nova@example.com")
        rater = User(username="simone", email="simone@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Golden Hour", artist="Solange K", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_friends_song_notifies_the_sharer(app, sharer_and_song):
    """
    Regression test for Issue #4.

    Before the fix, rate_song() saved the Rating but never called
    create_notification(), so the sharer never found out their song was
    rated. This test would have failed against the buggy version
    (0 notifications) and passes against the fix (1 notification).
    """
    with app.app_context():
        sharer = sharer_and_song["sharer"]
        rater = sharer_and_song["rater"]
        song = sharer_and_song["song"]

        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert rater.username in notifications[0]["body"]


def test_rating_your_own_song_does_not_self_notify(app, sharer_and_song):
    """Rating a song you shared yourself should not generate a notification."""
    with app.app_context():
        sharer = sharer_and_song["sharer"]
        song = sharer_and_song["song"]

        rate_song(sharer.id, song.id, 4)

        notifications = get_notifications(sharer.id)
        assert notifications == []
