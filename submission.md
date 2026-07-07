# Mixtape Bug Hunt — Submission

_(AI usage section is written last, per the assignment brief, and appears below the codebase map and RCA entries. See "AI Usage" near the end of this document.)_

## Codebase Map

### Main files and their roles

- **`app.py`** — Flask application factory (`create_app`). Configures the SQLAlchemy database URI (defaults to local `sqlite:///mixtape.db`), initializes the `db` extension, registers the four blueprints (`songs`, `playlists`, `users`, `feed`), and calls `db.create_all()` inside an app context. This is the single source of the shared `db` object that every model and service imports.

- **`models.py`** — Defines every SQLAlchemy model: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`, plus three association tables: `friendships` (symmetric many-to-many between users), `song_tags` (many-to-many between songs and tags), and `playlist_entries` (many-to-many between playlists and songs, but with extra columns — `position`, `added_by`, `added_at` — so it also records who added a song and in what order). Each model has a `to_dict()` used to serialize it for JSON responses.

- **`routes/`** — Thin Flask blueprints. Each route parses the request, calls exactly one service function, and formats the JSON response / status code. No business logic lives here.
  - `songs.py` — search, song detail, rating, listening events.
  - `playlists.py` — create playlist, get playlist detail/songs, add song to playlist.
  - `users.py` — user detail, streak, notifications (list + mark-read).
  - `feed.py` — "listening now" and general activity feed.

- **`services/`** — All business logic. This is where the five bugs live.
  - `streak_service.py` — increments/resets a user's daily listening streak.
  - `feed_service.py` — "Friends Listening Now" (recency-filtered) and the general activity feed (not recency-filtered).
  - `search_service.py` — title/artist search over songs.
  - `notification_service.py` — creates and retrieves `Notification` rows; called by the playlist and rating flows.
  - `playlist_service.py` — playlist CRUD/read logic, including the ordered song list for a playlist.

- **`seed_data.py`** — Populates the DB with 5 users/friendships, 25 songs with 0/1/3+ tags each (deliberately, to expose Issue #3), listening events at different recencies (to expose Issue #2), and 3 playlists (to expose Issue #5).

- **`tests/`** — `test_streaks.py`, `test_search.py`, `test_playlists.py`. These already contain assertions that pin down the *intended* correct behavior (e.g. `test_streak_increments_on_sunday`, `test_search_no_duplicates_multi_tag_song`, `test_playlist_returns_all_songs` — the last one's comment literally says "Bug causes this to return 4").

### Pattern I noticed

Every route delegates immediately to a service function and does nothing but input parsing/response formatting. All business logic — including all five bugs — lives in `services/`. The routes and models are essentially bug-free; nothing in this project required touching `routes/` or `models.py`.

### Data flow — a friend rates a shared song (working example, contrasted with the broken one)

1. Client sends `POST /songs/<song_id>/rate` with `{user_id, score}`.
2. `routes/songs.py::rate()` parses the body and calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the score (1–5), looks up the `Song` and rater `User`, then either updates an existing `Rating` row (unique on `user_id`+`song_id`) or inserts a new one, and commits.
4. The route returns the serialized `Rating`.

Compare this to the **working** playlist-add flow: `routes/playlists.py::add_song()` → `notification_service.add_to_playlist()`, which appends the song to the playlist **and then calls `create_notification()`** to tell the song's original sharer. `rate_song()` never makes that second call — it saves the `Rating` and returns, so the sharer is never told their song was rated. That gap is Issue #4, and it's a direct result of `rate_song()` not following the same two-step pattern (mutate + notify) that `add_to_playlist()` follows.


---

## Root Cause Analysis Entries

### Issue #1 — My listening streak keeps resetting

**How I reproduced it.** `services/streak_service.py::update_listening_streak(user, now)` is pure logic (it only reads/writes `user.listening_streak` and `user.last_listened_at`; it never touches `db.session`), so I could exercise the real, unmodified file directly instead of going through the Flask app (Flask/SQLAlchemy could not be installed in this sandbox — see AI/environment note at the end). I stubbed `sys.modules['app']` and `sys.modules['models']` with a bare `FakeUser` object exposing the same two attributes, imported the real `services/streak_service.py`, and called `update_listening_streak` twice: once with a Saturday timestamp, then a Sunday timestamp exactly one day later (the same scenario the repo's own `test_streak_increments_on_sunday` describes). Result before any fix: streak went `1 -> 1` instead of `1 -> 2` — reproduced.

**How I found the root cause.** I read `update_listening_streak` top to bottom. The branch that decides what happens after one day has passed is:
```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
else:
    user.listening_streak = 1
```
The moment I was confident this was the exact bug (not just a suspicious area) was checking what `datetime.weekday()` returns: Monday=0 ... Sunday=6. So `today.weekday() != 6` literally reads "today is not Sunday." That condition is unrelated to whether the *previous* day was skipped — `days_since_last == 1` already proves the two listens were on consecutive calendar days. Adding `and today.weekday() != 6` on top means: even when the two days are genuinely consecutive, if today happens to be a Sunday, the increment branch is skipped and execution falls into the `else`, which resets the streak to 1.

**The root cause.** The increment condition ANDs together two unrelated checks: "was this a consecutive day" (`days_since_last == 1`) and "today is not Sunday" (`today.weekday() != 6`). The second check has no logical connection to streak continuity — a user who listens Saturday and then Sunday has listened on two consecutive days and should get an incremented streak, but the `!= 6` clause specifically excludes Sunday from ever incrementing, so it always falls through to the reset branch on that one day of the week.

**My fix and side-effect check.** I removed the `and today.weekday() != 6` clause entirely, leaving `elif days_since_last == 1: user.listening_streak += 1`. This makes every consecutive-day listen increment the streak regardless of which weekday it lands on. I re-ran the extracted-logic harness against all five scenarios covered by `tests/test_streaks.py` (new user starts at 1, consecutive-day increment, same-day no double-count, skipped-day reset, and the Saturday→Sunday case) — all five pass with the one-line fix, and none of the other four behaviors changed, since the removed clause only ever mattered on the specific day `today.weekday() == 6`.
