import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

LEAGUE_ID = "1332851598593908736"

# Stable Sleeper user ID for Doctorie.
# We use owner_id rather than assuming roster_id stays constant.
USER_ID = "731759054447321088"

BASE_URL = "https://api.sleeper.app/v1"

OUTPUT_DIR = "docs"
STATE_FILE = os.path.join(OUTPUT_DIR, "league-state.json")
PLAYER_CACHE_FILE = os.path.join(OUTPUT_DIR, "player-map.json")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")


# ------------------------------------------------------------
# GENERIC HTTP HELPERS
# ------------------------------------------------------------

def fetch_json(url, headers=None, method="GET", body=None):
    request_headers = {
        "User-Agent": "fantasy-football-league-state/2.0",
        "Accept": "application/json",
    }

    if headers:
        request_headers.update(headers)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")

            if not raw:
                return None

            return json.loads(raw)

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} for {url}: {error_body}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Network error for {url}: {exc.reason}"
        ) from exc


def fetch_sleeper(path):
    url = f"{BASE_URL}{path}"
    print(f"Fetching {url}")
    return fetch_json(url)


# ------------------------------------------------------------
# PLAYER DATABASE
# ------------------------------------------------------------

def player_cache_is_fresh():
    if not os.path.exists(PLAYER_CACHE_FILE):
        return False

    modified = datetime.fromtimestamp(
        os.path.getmtime(PLAYER_CACHE_FILE),
        tz=timezone.utc,
    )

    age_hours = (
        datetime.now(timezone.utc) - modified
    ).total_seconds() / 3600

    return age_hours < 24


def load_players():
    if player_cache_is_fresh():
        print("Using existing player cache.")

        with open(
            PLAYER_CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    print("Refreshing Sleeper player database.")

    players = fetch_sleeper("/players/nfl")

    if not isinstance(players, dict):
        raise RuntimeError(
            "Sleeper player database did not return a dictionary."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(
        PLAYER_CACHE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            players,
            file,
            ensure_ascii=False,
        )

    return players


def player_info(player_id, players):
    player_id = str(player_id)

    data = players.get(player_id, {}) or {}

    full_name = data.get("full_name")

    if not full_name:
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        full_name = f"{first} {last}".strip()

    return {
        "player_id": player_id,
        "name": full_name or player_id,
        "position": data.get("position"),
        "team": data.get("team"),
        "status": data.get("status"),
    }


def safe_list(value):
    return value or []


def normalize_player_list(player_ids, players):
    return [
        player_info(player_id, players)
        for player_id in safe_list(player_ids)
        if player_id is not None
        and str(player_id) != "0"
    ]


# ------------------------------------------------------------
# ROSTER NORMALIZATION
# ------------------------------------------------------------

def normalize_rosters(rosters, users, players):
    users_by_id = {
        str(user.get("user_id")): user
        for user in users
        if user.get("user_id") is not None
    }

    normalized = []

    for roster in rosters:
        owner_id = roster.get("owner_id")

        if owner_id is not None:
            owner_id = str(owner_id)

        owner = users_by_id.get(owner_id, {}) or {}

        roster_player_ids = safe_list(roster.get("players"))
        starter_player_ids = safe_list(roster.get("starters"))
        taxi_player_ids = safe_list(roster.get("taxi"))
        reserve_player_ids = safe_list(roster.get("reserve"))

        roster_players = normalize_player_list(
            roster_player_ids,
            players,
        )

        starters = normalize_player_list(
            starter_player_ids,
            players,
        )

        taxi = normalize_player_list(
            taxi_player_ids,
            players,
        )

        reserve = normalize_player_list(
            reserve_player_ids,
            players,
        )

        starter_ids = {
            str(player_id)
            for player_id in starter_player_ids
            if player_id is not None
            and str(player_id) != "0"
        }

        taxi_ids = {
            str(player_id)
            for player_id in taxi_player_ids
            if player_id is not None
            and str(player_id) != "0"
        }

        reserve_ids = {
            str(player_id)
            for player_id in reserve_player_ids
            if player_id is not None
            and str(player_id) != "0"
        }

        bench = [
            player
            for player in roster_players
            if player["player_id"] not in starter_ids
            and player["player_id"] not in taxi_ids
            and player["player_id"] not in reserve_ids
        ]

        metadata = owner.get("metadata") or {}

        normalized.append({
            "roster_id": roster.get("roster_id"),
            "owner_id": owner_id,
            "display_name": owner.get("display_name"),
            "team_name": metadata.get("team_name"),
            "players": roster_players,
            "starters": starters,
            "bench": bench,
            "taxi": taxi,
            "reserve": reserve,
            "settings": roster.get("settings") or {},
        })

    return normalized


def find_my_roster(normalized_rosters):
    my_roster = next(
        (
            roster
            for roster in normalized_rosters
            if roster["owner_id"] == USER_ID
        ),
        None,
    )

    if my_roster is None:
        raise RuntimeError(
            "Could not find a roster owned by Sleeper "
            f"user ID {USER_ID}."
        )

    if not my_roster.get("players"):
        raise RuntimeError(
            "Doctorie roster was found but contains no players."
        )

    return my_roster


# ------------------------------------------------------------
# SUPABASE HELPERS
# ------------------------------------------------------------

def validate_supabase_configuration():
    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL environment variable is missing."
        )

    if not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY environment variable is missing."
        )


def supabase_headers(prefer=None):
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    return headers


def supabase_request(
    table,
    method="GET",
    body=None,
    query="",
    prefer=None,
):
    url = f"{SUPABASE_URL}/rest/v1/{table}"

    if query:
        url = f"{url}?{query}"

    return fetch_json(
        url,
        headers=supabase_headers(prefer),
        method=method,
        body=body,
    )


# ------------------------------------------------------------
# SUPABASE PUBLISHING
# ------------------------------------------------------------

def build_roster_player_rows(my_roster, generated_at):
    slot_by_player_id = {}

    for player in my_roster.get("players", []):
        slot_by_player_id[player["player_id"]] = "roster"

    for player in my_roster.get("bench", []):
        slot_by_player_id[player["player_id"]] = "bench"

    for player in my_roster.get("taxi", []):
        slot_by_player_id[player["player_id"]] = "taxi"

    for player in my_roster.get("reserve", []):
        slot_by_player_id[player["player_id"]] = "reserve"

    for player in my_roster.get("starters", []):
        slot_by_player_id[player["player_id"]] = "starter"

    rows = []

    for player in my_roster.get("players", []):
        rows.append({
            "player_id": player["player_id"],
            "league_id": LEAGUE_ID,
            "owner_user_id": USER_ID,
            "roster_id": my_roster.get("roster_id"),
            "display_name": my_roster.get("display_name"),
            "player_name": player.get("name"),
            "position": player.get("position"),
            "nfl_team": player.get("team"),
            "player_status": player.get("status"),
            "roster_slot": slot_by_player_id.get(
                player["player_id"],
                "roster",
            ),
            "source_generated_at_utc": generated_at,
            "updated_at": generated_at,
        })

    return rows


def publish_league_state_to_supabase(state):
    print("Publishing league state to Supabase.")

    validate_supabase_configuration()

    generated_at = state["generated_at_utc"]
    my_roster = state["my_roster"]

    league_state_row = {
        "id": 1,
        "league_id": LEAGUE_ID,
        "user_id": USER_ID,
        "source": "Sleeper API",
        "generated_at_utc": generated_at,
        "refreshed_at": generated_at,
        "payload": state,
    }

    supabase_request(
        table="league_state",
        method="POST",
        body=league_state_row,
        query="on_conflict=id",
        prefer="resolution=merge-duplicates,return=minimal",
    )

    # Remove only this user's previous roster snapshot before
    # inserting the newly normalized roster.
    supabase_request(
        table="roster_players",
        method="DELETE",
        query=f"owner_user_id=eq.{USER_ID}",
        prefer="return=minimal",
    )

    roster_rows = build_roster_player_rows(
        my_roster,
        generated_at,
    )

    if not roster_rows:
        raise RuntimeError(
            "No roster rows were created for Supabase."
        )

    supabase_request(
        table="roster_players",
        method="POST",
        body=roster_rows,
        prefer="return=minimal",
    )

    print(
        "Supabase publish successful: "
        f"{len(roster_rows)} roster players uploaded."
    )


# ------------------------------------------------------------
# VALIDATION
# ------------------------------------------------------------

def validate_state(state):
    my_roster = state.get("my_roster")

    if not my_roster:
        raise RuntimeError(
            "Generated league state has no my_roster section."
        )

    if my_roster.get("owner_id") != USER_ID:
        raise RuntimeError(
            "Generated roster owner_id does not match "
            f"configured user ID {USER_ID}."
        )

    if not my_roster.get("display_name"):
        raise RuntimeError(
            "Generated roster has no display name."
        )

    if not my_roster.get("players"):
        raise RuntimeError(
            "Generated roster contains no active players."
        )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True,
    )

    league = fetch_sleeper(
        f"/league/{LEAGUE_ID}"
    )

    users = fetch_sleeper(
        f"/league/{LEAGUE_ID}/users"
    )

    rosters = fetch_sleeper(
        f"/league/{LEAGUE_ID}/rosters"
    )

    traded_picks = fetch_sleeper(
        f"/league/{LEAGUE_ID}/traded_picks"
    )

    nfl_state = fetch_sleeper(
        "/state/nfl"
    )

    players = load_players()

    if not isinstance(users, list):
        raise RuntimeError(
            "Sleeper users endpoint did not return a list."
        )

    if not isinstance(rosters, list):
        raise RuntimeError(
            "Sleeper rosters endpoint did not return a list."
        )

    normalized_rosters = normalize_rosters(
        rosters,
        users,
        players,
    )

    my_roster = find_my_roster(
        normalized_rosters
    )

    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    state = {
        "schema_version": "2.0",
        "generated_at_utc": generated_at,
        "source": "Sleeper API",
        "league_id": LEAGUE_ID,
        "user_id": USER_ID,
        "freshness": {
            "generated_at_utc": generated_at,
            "refresh_target_minutes": 15,
        },
        "nfl_state": nfl_state,
        "league": league,
        "my_roster": my_roster,
        "all_rosters": normalized_rosters,
        "traded_picks": traded_picks or [],
    }

    validate_state(state)

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        "Success: found roster "
        f"{my_roster['roster_id']} "
        f"for {my_roster['display_name']}."
    )

    print(
        "Roster contains "
        f"{len(my_roster['players'])} roster players, "
        f"{len(my_roster['taxi'])} taxi players, and "
        f"{len(my_roster['reserve'])} reserve players."
    )

    print(
        f"Wrote {STATE_FILE}"
    )

    # Supabase is intentionally treated as required.
    # If publishing fails, the GitHub Action should fail rather
    # than silently pretending the database is current.
    publish_league_state_to_supabase(state)

    print(
        "League state refresh completed successfully."
    )


if __name__ == "__main__":
    main()
